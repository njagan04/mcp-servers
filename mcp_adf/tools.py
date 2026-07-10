import hashlib
import json
import re
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path

from azure.core.exceptions import ResourceNotFoundError
from azure.mgmt.datafactory import DataFactoryManagementClient
from azure.mgmt.datafactory.models import (
    DataFlowResource,
    DatasetResource,
    GlobalParameterResource,
    LinkedServiceResource,
    PipelineResource,
    RunFilterParameters,
    RunQueryFilter,
)

from mcp_adf.auth import get_credential

# Rollback snapshots: content-addressed, like git. Each resource's definition content is
# stored once per unique hash in "_blobs/<hash>.json"; an append-only "_index.jsonl" records
# the ordered timeline of checkpoints (timestamp/reason/state_name/hash), so re-visiting
# identical content (e.g. A -> B -> A) reuses the existing blob instead of duplicating it.
# A per-resource "_cursor.json" tracks which checkpoint the live Azure resource currently
# matches, independent of the index, so back_*/forward_* can move through history like
# `git checkout` without ever rewriting the index. project/_snapshot/, sibling to mcp_adf/
# under the project root (same placement convention as project/_logs/ in audit.py).
_SNAPSHOT_DIR = Path(__file__).parents[1] / "project" / "_snapshot"


def _client(tenant_id: str, client_id: str, client_secret: str, subscription_id: str) -> DataFactoryManagementClient:
    return DataFactoryManagementClient(get_credential(tenant_id, client_id, client_secret), subscription_id)


def _to_wire_dict(resource) -> dict:
    """
    True wire-format dict (camelCase, e.g. "dependsOn", "typeProperties.waitTimeInSeconds")
    — the shape PipelineResource/DatasetResource/DataFlowResource.deserialize() actually
    expects, and the same shape ADF Studio's "Code" view / ARM export uses.

    NOT the same as `.as_dict()`, which uses Python attribute names (snake_case) and is
    UNSAFE to feed back into `.deserialize()`: any field whose wire name differs from its
    Python attribute name (dependsOn/depends_on, waitTimeInSeconds/wait_time_in_seconds,
    variableName/variable_name, errorCode/error_code, ...) silently vanishes into an inert
    `additional_properties` bucket instead of the real attribute, which then serializes
    to ADF as empty/missing — activities appear created but aren't actually connected,
    and typeProperties like variable names or error codes are silently dropped.
    """
    serialized = resource.serialize(keep_readonly=True)
    return serialized.get("properties", serialized)


def _find_miscased_fields(obj, path: str = "") -> list[str]:
    """
    Recursively walks a deserialized SDK model object tree looking for
    `additional_properties` keys that are a miscased (snake_case) version of a REAL
    attribute the model has under a different, correctly-cased name — the exact failure
    mode that caused the dependsOn/typeProperties bug (see _to_wire_dict): a caller-supplied
    key `Model.deserialize()` doesn't recognize is silently dropped into
    `additional_properties` instead of raising, so the field never reaches ADF, with no
    error to signal it. Returns human-readable warnings; empty list if nothing looks wrong.

    Deliberately does NOT flag every `additional_properties` entry — ADF genuinely allows
    arbitrary custom properties on activities. Only flags a key that EXACTLY matches a real
    Python attribute name this object type has (e.g. "depends_on", "wait_time_in_seconds").
    That's the unambiguous signature of the bug: `.as_dict()`'s output (Python attribute
    names) fed into `.deserialize()`, which only recognizes wire keys (e.g. "dependsOn") —
    a genuine custom property would essentially never happen to collide with a real
    attribute's exact Python name.
    """
    warnings: list[str] = []
    if obj is None:
        return warnings
    if isinstance(obj, list):
        for i, item in enumerate(obj):
            warnings.extend(_find_miscased_fields(item, f"{path}[{i}]"))
        return warnings
    if isinstance(obj, dict):
        for k, v in obj.items():
            warnings.extend(_find_miscased_fields(v, f"{path}.{k}" if path else k))
        return warnings

    attribute_map = getattr(obj, "_attribute_map", None)
    if attribute_map is None:
        return warnings

    extra = getattr(obj, "additional_properties", None) or {}
    real_names = set(attribute_map.keys()) - {"additional_properties"}
    for key, extra_value in extra.items():
        if key not in real_names or not extra_value:
            continue
        # Some compound wire keys (e.g. "properties.activities" on PipelineResource
        # deserialized from a bare/unwrapped dict) get correctly flattened into the real
        # attribute by msrest's deserializer AND redundantly echoed into
        # additional_properties — a benign quirk, not dropped data. Distinguish that case
        # from a genuine drop by checking whether the real attribute actually ended up
        # populated: if it did, this is the harmless echo; if it's still empty/None while
        # additional_properties holds a real value, the field was genuinely never applied.
        if getattr(obj, key, None):
            continue
        correct_wire_key = attribute_map[key]["key"]
        warnings.append(
            f'{path or "root"}: key "{key}" was silently ignored — it looks like a '
            f'miscased version of the real field (wire key "{correct_wire_key}"; ADF '
            f'uses camelCase, not snake_case). This value will NOT be applied.'
        )

    for attr_name in real_names:
        if attr_name == "additional_properties":
            continue
        try:
            value = getattr(obj, attr_name)
        except AttributeError:
            continue
        warnings.extend(_find_miscased_fields(value, f"{path}.{attr_name}" if path else attr_name))
    return warnings


def _reject_if_miscased(resource, resource_label: str) -> dict | None:
    """
    Returns an error dict (never write anything to ADF) if the just-deserialized resource
    has any miscased-field warnings, else None. Call this right after every
    `Model.deserialize()` and before the mutating API call that would otherwise silently
    write incomplete data.
    """
    warnings = _find_miscased_fields(resource)
    if not warnings:
        return None
    return {
        "error": "possible_miscased_fields",
        "resource": resource_label,
        "warnings": warnings,
        "hint": 'ADF wire format is camelCase (e.g. "dependsOn", "typeProperties.waitTimeInSeconds"), '
                "not Python-style snake_case. Fix the flagged keys and retry — nothing was written to ADF.",
    }


def list_pipelines(
    factory_name: str,
    subscription_id: str, resource_group: str,
    tenant_id: str, client_id: str, client_secret: str,
) -> dict:
    """List all pipeline names in the data factory."""
    client = _client(tenant_id, client_id, client_secret, subscription_id)
    pipelines = client.pipelines.list_by_factory(resource_group, factory_name)
    return {"pipelines": [p.name for p in pipelines]}


def create_pipeline(
    pipeline_name: str, factory_name: str,
    subscription_id: str, resource_group: str,
    tenant_id: str, client_id: str, client_secret: str,
    definition: dict,
    reason: str,
    state_name: str | None = None,
) -> dict:
    """
    Creates a brand-new pipeline. Fails with an explicit error if a pipeline with this
    name already exists — use update_pipeline_definition to modify an existing pipeline
    instead. Records two checkpoints in this pipeline's history: "before-creation" (the
    resource didn't exist — rollback here deletes it) and one for the just-created content,
    named `state_name` if given (default "created") — so list_pipeline_snapshots and
    rollback_pipeline_definition work on it from the start, same as any updated pipeline.

    `definition` accepts either shape:
      - the flat shape get_pipeline_definition_raw/update_pipeline_definition use
        (activities/parameters/description/... at the top level), or
      - the ARM / Data Factory Studio export shape
        ({"name": ..., "properties": {"activities": [...], ...}}) — if a "properties"
        key is present, its contents are used and the wrapper (including its own "name")
        is discarded. `pipeline_name` is always what determines the actual name created.
    """
    client = _client(tenant_id, client_id, client_secret, subscription_id)

    try:
        client.pipelines.get(resource_group, factory_name, pipeline_name)
        return {"error": "pipeline_already_exists", "pipeline_name": pipeline_name}
    except ResourceNotFoundError:
        pass

    _push_snapshot(
        "pipeline", factory_name, pipeline_name,
        definition=None, reason=reason, change_summary="pipeline did not exist",
        state_name="before-creation", action="create",
    )

    properties = definition.get("properties", definition)
    pipeline_resource = PipelineResource.deserialize(properties)
    error = _reject_if_miscased(pipeline_resource, "pipeline")
    if error:
        return error
    created = client.pipelines.create_or_update(resource_group, factory_name, pipeline_name, pipeline_resource)

    saved = _push_snapshot(
        "pipeline", factory_name, pipeline_name,
        definition=_to_wire_dict(created), reason=reason, change_summary="pipeline created",
        state_name=state_name or "created",
    )
    return {
        "pipeline_name": pipeline_name,
        "created": True,
        "reason": reason,
        "saved_state_name": saved["state_name"],
        "etag": created.etag,
    }


def _get_failed_activities_for_run(
    client: DataFactoryManagementClient,
    resource_group: str,
    factory_name: str,
    run_id: str,
    window_start: datetime,
    window_end: datetime,
) -> list:
    """Fetch failed activity runs for a known run_id."""
    activity_filter = RunFilterParameters(
        last_updated_after=window_start,
        last_updated_before=window_end,
    )
    activities = client.activity_runs.query_by_pipeline_run(
        resource_group, factory_name, run_id, activity_filter
    )
    return [a for a in activities.value if a.status == "Failed"]


def _follow_single_chain(
    client: DataFactoryManagementClient,
    resource_group: str,
    factory_name: str,
    root_step: dict,
    window_start: datetime,
    window_end: datetime,
    max_depth: int,
) -> dict:
    """Follow one ExecutePipeline chain from a known starting step to its leaf failure."""
    execution_path = [root_step]
    current_run_id = root_step.pop("_child_run_id", None)
    current_pipeline = root_step.pop("_child_pipeline", "unknown")

    if not current_run_id:
        return {"execution_path": execution_path, "leaf": _make_leaf(root_step)}

    for _ in range(max_depth):
        failed = _get_failed_activities_for_run(
            client, resource_group, factory_name, current_run_id, window_start, window_end
        )
        if not failed:
            break
        activity = failed[0]
        step = {
            "pipeline_name": current_pipeline,
            "run_id": current_run_id,
            "activity_name": activity.activity_name,
            "activity_type": activity.activity_type,
            "activity_run_id": activity.activity_run_id,
            "error": activity.error,
        }
        execution_path.append(step)
        if activity.activity_type != "ExecutePipeline":
            break
        child_output = activity.output or {}
        child_run_id = child_output.get("pipelineRunId")
        child_pipeline = child_output.get("pipelineName", "unknown")
        if not child_run_id:
            break
        current_run_id = child_run_id
        current_pipeline = child_pipeline

    leaf_step = execution_path[-1]
    return {"execution_path": execution_path, "leaf": _make_leaf(leaf_step)}


def _make_leaf(step: dict) -> dict:
    err = step.get("error") or {}

    def _err_field(key: str, attr: str) -> str | None:
        return err.get(key) if isinstance(err, dict) else getattr(err, attr, None)

    return {
        "pipeline_name": step["pipeline_name"],
        "run_id": step["run_id"],
        "activity_name": step["activity_name"],
        "activity_type": step["activity_type"],
        "activity_run_id": step.get("activity_run_id"),
        "error_code": _err_field("errorCode", "error_code"),
        "failure_type": _err_field("failureType", "failure_type"),
        "error_source": _err_field("target", "target"),
        "message": _err_field("message", "message"),
    }


def _resolve_nested_failure(
    client: DataFactoryManagementClient,
    resource_group: str,
    factory_name: str,
    run_id: str,
    pipeline_name: str,
    window_start: datetime,
    window_end: datetime,
    max_depth: int = 5,
) -> dict:
    """
    Resolve ALL failed activity branches from a given run_id. Handles parallel activity
    failures by following each failed branch independently (fan-out support).

    Returns:
      - execution_path / depth / leaf  → primary branch (first failed activity)
      - failed_branches                → list of {execution_path, leaf} for every failed branch
    """
    failed_activities = _get_failed_activities_for_run(
        client, resource_group, factory_name, run_id, window_start, window_end
    )
    if not failed_activities:
        return {"run_id": run_id, "execution_path": [], "leaf": None, "failed_branches": []}

    branches: list[dict] = []
    for activity in failed_activities:
        step = {
            "pipeline_name": pipeline_name,
            "run_id": run_id,
            "activity_name": activity.activity_name,
            "activity_type": activity.activity_type,
            "activity_run_id": activity.activity_run_id,
            "error": activity.error,
        }
        if activity.activity_type == "ExecutePipeline":
            output = activity.output or {}
            child_run_id = output.get("pipelineRunId")
            child_pipeline = output.get("pipelineName", "unknown")
            step["_child_run_id"] = child_run_id
            step["_child_pipeline"] = child_pipeline
            branch = _follow_single_chain(
                client, resource_group, factory_name,
                step, window_start, window_end, max_depth - 1,
            )
        else:
            branch = {"execution_path": [step], "leaf": _make_leaf(step)}
        branch["depth"] = len(branch["execution_path"])
        branches.append(branch)

    if not branches:
        return {"run_id": run_id, "execution_path": [], "leaf": None, "failed_branches": []}

    primary = branches[0]
    return {
        "run_id": run_id,
        "execution_path": primary["execution_path"],
        "depth": primary["depth"],
        "leaf": primary["leaf"],
        "failed_branches": branches,
    }


def get_activity_run_error(
    pipeline_name: str, factory_name: str, event_timestamp: str,
    subscription_id: str, resource_group: str,
    tenant_id: str, client_id: str, client_secret: str,
) -> dict:
    """
    Resolves run_id from pipeline_name + event_timestamp, then recursively follows
    any ExecutePipeline activity chains until the leaf failed activity is found.

    Example for Master → Pipeline B → Pipeline C → Copy Activity (Failed):
    Returns the full execution path so the classifier and investigator see the real
    root cause, not just "Execute Pipeline activity failed."
    """
    client = _client(tenant_id, client_id, client_secret, subscription_id)

    ts = datetime.fromisoformat(event_timestamp).replace(tzinfo=timezone.utc)
    window_start = ts - timedelta(minutes=5)
    window_end = ts + timedelta(minutes=30)

    run_filter = RunFilterParameters(
        last_updated_after=window_start,
        last_updated_before=window_end,
        filters=[RunQueryFilter(operand="PipelineName", operator="Equals", values=[pipeline_name])],
    )
    runs = client.pipeline_runs.query_by_factory(resource_group, factory_name, run_filter)
    failed_runs = [r for r in runs.value if r.status == "Failed"]
    if not failed_runs:
        return {"error": "no_failed_run_found"}

    master_run_id: str | None = failed_runs[0].run_id
    if not master_run_id:
        return {"error": "run_id_unavailable"}
    return _resolve_nested_failure(
        client, resource_group, factory_name, master_run_id, pipeline_name,
        window_start, window_end,
    )


def list_activity_runs(
    run_id: str, factory_name: str,
    subscription_id: str, resource_group: str,
    tenant_id: str, client_id: str, client_secret: str,
    days: int = 30,
) -> dict:
    """
    Lists every activity in a specific pipeline run — name, type, status, timing, and
    activity_run_id for each. Matches what ADF Studio's monitoring view shows when you
    drill into a run's activity list. Deliberately lightweight: does NOT include
    input/output (can be large, e.g. a Lookup's full returned rows) — call
    get_activity_run_io with a specific activity_run_id from this list to see full
    input/output for just the activities that matter.
    """
    client = _client(tenant_id, client_id, client_secret, subscription_id)
    now = datetime.now(timezone.utc)
    activity_filter = RunFilterParameters(
        last_updated_after=now - timedelta(days=days),
        last_updated_before=now,
    )
    activities = client.activity_runs.query_by_pipeline_run(resource_group, factory_name, run_id, activity_filter)
    return {
        "run_id": run_id,
        "activities": [
            {
                "activity_run_id": a.activity_run_id,
                "activity_name": a.activity_name,
                "activity_type": a.activity_type,
                "status": a.status,
                "start": str(a.activity_run_start),
                "end": str(a.activity_run_end),
                "duration_in_ms": a.duration_in_ms,
            }
            for a in activities.value
        ],
    }


def get_activity_run_io(
    activity_run_id: str, run_id: str, factory_name: str,
    subscription_id: str, resource_group: str,
    tenant_id: str, client_id: str, client_secret: str,
    days: int = 30,
) -> dict:
    """
    Raw input/output payload for one specific activity run — not aggregated
    (get_activity_run_history), not just the error (get_activity_run_error), but the
    actual resolved input parameters and captured output ADF recorded for that exact
    execution. Often the concrete evidence a bare error message doesn't show: the real
    query a Copy/Lookup activity ran after parameter/expression substitution, rows
    affected, or a Lookup's actual returned data — usually essential for `business_logic`/
    `config` diagnosis where the failure is about what data was involved, not just that
    something failed.

    ADF has no "get activity run by id" API — this queries every activity run for the
    given pipeline `run_id` within the last `days` days and picks out the one matching
    `activity_run_id`. Both ids are already surfaced by get_activity_run_error's
    leaf/execution_path, so this is a natural follow-up call, not a fresh lookup.
    """
    client = _client(tenant_id, client_id, client_secret, subscription_id)
    now = datetime.now(timezone.utc)
    activity_filter = RunFilterParameters(
        last_updated_after=now - timedelta(days=days),
        last_updated_before=now,
    )
    activities = client.activity_runs.query_by_pipeline_run(resource_group, factory_name, run_id, activity_filter)
    for a in activities.value:
        if a.activity_run_id == activity_run_id:
            return {
                "activity_run_id": activity_run_id,
                "activity_name": a.activity_name,
                "activity_type": a.activity_type,
                "status": a.status,
                "input": a.input,
                "output": a.output,
                "error": a.error,
            }
    return {"error": "activity_run_not_found", "activity_run_id": activity_run_id, "run_id": run_id}


def get_pipeline_run_status(
    run_id: str, factory_name: str,
    subscription_id: str, resource_group: str,
    tenant_id: str, client_id: str, client_secret: str,
) -> dict:
    """Freshness check before executing an approved rerun."""
    client = _client(tenant_id, client_id, client_secret, subscription_id)
    run = client.pipeline_runs.get(resource_group, factory_name, run_id)
    return {"run_id": run_id, "status": run.status, "message": run.message}


def get_pipeline_run_history(
    pipeline_name: str, factory_name: str,
    subscription_id: str, resource_group: str,
    tenant_id: str, client_id: str, client_secret: str,
    days: int = 7,
) -> dict:
    """Evidence loop — recent run patterns for a pipeline."""
    client = _client(tenant_id, client_id, client_secret, subscription_id)
    now = datetime.now(timezone.utc)
    run_filter = RunFilterParameters(
        last_updated_after=now - timedelta(days=days),
        last_updated_before=now,
        filters=[RunQueryFilter(operand="PipelineName", operator="Equals", values=[pipeline_name])],
    )
    runs = client.pipeline_runs.query_by_factory(resource_group, factory_name, run_filter)
    return {
        "runs": [
            {
                "run_id": r.run_id,
                "status": r.status,
                "start": str(r.run_start),
                "end": str(r.run_end),
                "triggered_by": {
                    "name": getattr(r.invoked_by, "name", None),
                    "type": getattr(r.invoked_by, "invoked_by_type", None),
                } if r.invoked_by else None,
            }
            for r in runs.value
        ]
    }


def get_pipeline_definition(
    pipeline_name: str, factory_name: str,
    subscription_id: str, resource_group: str,
    tenant_id: str, client_id: str, client_secret: str,
) -> dict:
    """
    Evidence loop — pipeline structure with activity types and ExecutePipeline references only.
    For a failing activity's timeout policy, dataset/linked-service references, or query —
    the fields that usually explain WHY it failed — use get_pipeline_definition_raw instead.
    """
    client = _client(tenant_id, client_id, client_secret, subscription_id)
    pipeline = client.pipelines.get(resource_group, factory_name, pipeline_name)
    if pipeline is None:
        return {"name": pipeline_name, "activities": []}
    activities = []
    for a in (pipeline.activities or []):
        activity_type = getattr(a, "type", None) or getattr(a, "activity_type", "unknown")
        entry: dict = {"name": a.name, "type": activity_type}
        if activity_type == "ExecutePipeline":
            try:
                props = getattr(a, "type_properties", None)
                pipeline_ref = getattr(getattr(props, "pipeline", None), "reference_name", None)
                if pipeline_ref:
                    entry["references_pipeline"] = pipeline_ref
            except Exception:
                pass
        activities.append(entry)
    return {"name": pipeline.name, "activities": activities}


def get_activity_run_history(
    pipeline_name: str, factory_name: str,
    subscription_id: str, resource_group: str,
    tenant_id: str, client_id: str, client_secret: str,
    days: int = 7,
) -> dict:
    """
    Returns an aggregated summary of which activities have failed in recent runs of a pipeline.

    Instead of returning raw per-run activity data (expensive in tokens), this aggregates across
    up to 5 recent failed runs and returns per-activity failure counts + most recent error code.
    Useful for identifying recurring activity-level failures without high token cost.

    Returns:
      {
        "pipeline_name": ...,
        "runs_checked": N,
        "failed_activity_summary": [
          {"activity_name": "CopyToSilver", "failure_count": 3,
           "last_error_code": "UserErrorInvalidCredentials", "last_failed_at": "..."},
          ...
        ]
      }
    """
    client = _client(tenant_id, client_id, client_secret, subscription_id)
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(days=days)
    window_end = now

    run_filter = RunFilterParameters(
        last_updated_after=window_start,
        last_updated_before=window_end,
        filters=[RunQueryFilter(operand="PipelineName", operator="Equals", values=[pipeline_name])],
    )
    runs = client.pipeline_runs.query_by_factory(resource_group, factory_name, run_filter)
    failed_runs = [r for r in runs.value if r.status == "Failed"][:5]  # cap at 5 most recent

    # Aggregate: activity_name → {failure_count, last_error_code, last_failed_at}
    aggregated: dict[str, dict] = {}
    for run in failed_runs:
        if not run.run_id:
            continue
        try:
            failed_activities = _get_failed_activities_for_run(
                client, resource_group, factory_name, run.run_id, window_start, window_end
            )
        except Exception:
            continue
        for a in failed_activities:
            name = a.activity_name or "unknown"
            err = a.error or {}

            def _err_field(key: str, attr: str, err=err) -> str | None:
                return err.get(key) if isinstance(err, dict) else getattr(err, attr, None)

            failed_at = str(a.activity_run_end or a.activity_run_start or "")
            if name not in aggregated:
                aggregated[name] = {
                    "failure_count": 0,
                    "last_error_code": None,
                    "last_failure_type": None,
                    "last_error_source": None,
                    "last_activity_run_id": None,
                    "last_failed_at": "",
                }
            aggregated[name]["failure_count"] += 1
            if failed_at >= aggregated[name]["last_failed_at"]:
                aggregated[name]["last_error_code"] = _err_field("errorCode", "error_code")
                aggregated[name]["last_failure_type"] = _err_field("failureType", "failure_type")
                aggregated[name]["last_error_source"] = _err_field("target", "target")
                aggregated[name]["last_activity_run_id"] = a.activity_run_id
                aggregated[name]["last_failed_at"] = failed_at

    summary = [
        {
            "activity_name": name,
            "failure_count": data["failure_count"],
            "last_error_code": data["last_error_code"],
            "last_failure_type": data["last_failure_type"],
            "last_error_source": data["last_error_source"],
            "last_activity_run_id": data["last_activity_run_id"],
            "last_failed_at": data["last_failed_at"],
        }
        for name, data in sorted(aggregated.items(), key=lambda x: -x[1]["failure_count"])
    ]
    return {
        "pipeline_name": pipeline_name,
        "runs_checked": len(failed_runs),
        "failed_activity_summary": summary,
    }


def get_linked_service(
    service_name: str, factory_name: str,
    subscription_id: str, resource_group: str,
    tenant_id: str, client_id: str, client_secret: str,
) -> dict:
    """
    Evidence loop — name and type only (e.g. "AzureSqlDatabase"). Does NOT include the
    actual configured host/port/connection string — those live in typeProperties, which
    this deliberately lightweight tool omits. Use get_linked_service_definition_raw for
    the real connection details (needed for `network`/`config` diagnosis), or to get an
    editable structure for update_linked_service_definition.
    """
    client = _client(tenant_id, client_id, client_secret, subscription_id)
    svc = client.linked_services.get(resource_group, factory_name, service_name)
    if svc is None:
        return {"name": service_name, "type": "unknown"}
    return {"name": svc.name, "type": svc.properties.type if svc.properties else "unknown"}


def list_linked_services(
    factory_name: str,
    subscription_id: str, resource_group: str,
    tenant_id: str, client_id: str, client_secret: str,
) -> dict:
    """
    Factory-wide linked-service sweep, vs. get_linked_service's single lookup. Useful when
    the failing linked service isn't known by name up front (e.g. a `network` or
    `credential_expired` error only names a dataset or activity, not the linked service).
    """
    client = _client(tenant_id, client_id, client_secret, subscription_id)
    services = client.linked_services.list_by_factory(resource_group, factory_name)
    return {
        "linked_services": [
            {"name": s.name, "type": s.properties.type if s.properties else "unknown"}
            for s in services
        ]
    }


def get_linked_service_definition_raw(
    service_name: str, factory_name: str,
    subscription_id: str, resource_group: str,
    tenant_id: str, client_id: str, client_secret: str,
) -> dict:
    """
    Full linked-service definition — the actual configured host/port/connection string
    live in typeProperties (e.g. AzureSqlDatabase's typeProperties.connectionString), which
    get_linked_service deliberately omits. Use this during diagnosis of `network`/`config`
    failures to see the real configured server address, and as the editable structure to
    feed back into update_linked_service_definition (with edits applied) to apply a fix.
    """
    client = _client(tenant_id, client_id, client_secret, subscription_id)
    svc = client.linked_services.get(resource_group, factory_name, service_name)
    return _to_wire_dict(svc)


def update_linked_service_definition(
    service_name: str, factory_name: str,
    subscription_id: str, resource_group: str,
    tenant_id: str, client_id: str, client_secret: str,
    definition: dict,
    reason: str,
    change_summary: str,
    state_name: str | None = None,
) -> dict:
    """
    Overwrites a linked service's full definition (e.g. to correct a wrong host/port in
    a `network`/`config` failure). If this linked service has no history yet, its as-found
    content is captured as an "initial" checkpoint first. After applying the change, the
    NEW content is pushed as a named checkpoint — see update_pipeline_definition for the
    full history design. `definition` should be get_linked_service_definition_raw's output
    with edits applied.
    """
    client = _client(tenant_id, client_id, client_secret, subscription_id)
    current = client.linked_services.get(resource_group, factory_name, service_name)
    _ensure_baseline("linkedservice", factory_name, service_name, _to_wire_dict(current), reason)

    linked_service_resource = LinkedServiceResource.deserialize({"properties": definition})
    error = _reject_if_miscased(linked_service_resource, "linked service")
    if error:
        return error
    updated = client.linked_services.create_or_update(resource_group, factory_name, service_name, linked_service_resource)

    saved = _push_snapshot(
        "linkedservice", factory_name, service_name,
        definition=_to_wire_dict(updated), reason=reason, change_summary=change_summary,
        state_name=state_name,
    )
    return {
        "service_name": service_name,
        "updated": True,
        "reason": reason,
        "change_summary": change_summary,
        "saved_state_name": saved["state_name"],
        "etag": updated.etag,
    }


def list_linked_service_snapshots(
    service_name: str, factory_name: str,
    subscription_id: str, resource_group: str,
    tenant_id: str, client_id: str, client_secret: str,
) -> dict:
    """Lists every named checkpoint saved for this linked service, oldest first."""
    return {"service_name": service_name, "states": _list_snapshots("linkedservice", factory_name, service_name)}


def rollback_linked_service_definition(
    service_name: str, factory_name: str,
    subscription_id: str, resource_group: str,
    tenant_id: str, client_id: str, client_secret: str,
    reason: str,
    state_name: str,
    confirm_delete: bool = False,
) -> dict:
    """
    Jumps directly to any named checkpoint from list_linked_service_snapshots, regardless
    of where it sits in history — nothing is ever deleted. For simple one-step undo/redo
    without needing a state_name, use back_linked_service_definition /
    forward_linked_service_definition instead.

    If the target checkpoint predates the linked service's creation, applying it means
    DELETING it — this returns {"requires_confirmation": true} instead of deleting; call
    again with confirm_delete=true only after the human has agreed to that.
    """
    client = _client(tenant_id, client_id, client_secret, subscription_id)

    target = _find_snapshot("linkedservice", factory_name, service_name, state_name)
    if target is None:
        return {"error": "state_not_found", "service_name": service_name, "state_name": state_name}

    if target["action"] == "create":
        if not confirm_delete:
            return {
                "requires_confirmation": True,
                "would": "delete",
                "service_name": service_name,
                "target_state": target["state_name"],
                "message": (
                    f"'{target['state_name']}' predates this linked service's creation — rolling "
                    "back to it means DELETING it in Azure. Ask the human before proceeding; call "
                    "again with confirm_delete=true only after they agree."
                ),
            }
        client.linked_services.delete(resource_group, factory_name, service_name)
        _push_snapshot(
            "linkedservice", factory_name, service_name,
            definition=None, reason=reason, change_summary=f"rolled back to '{target['state_name']}'",
            state_name=target["state_name"], action="create",
        )
        return {"service_name": service_name, "rolled_back_to": target["state_name"], "deleted": True, "reason": reason}

    linked_service_resource = LinkedServiceResource.deserialize({"properties": target["definition"]})
    error = _reject_if_miscased(linked_service_resource, "linked service")
    if error:
        return error
    updated = client.linked_services.create_or_update(resource_group, factory_name, service_name, linked_service_resource)
    _push_snapshot(
        "linkedservice", factory_name, service_name,
        definition=_to_wire_dict(updated), reason=reason, change_summary=f"rolled back to '{target['state_name']}'",
        state_name=target["state_name"],
    )
    return {"service_name": service_name, "rolled_back_to": target["state_name"], "reason": reason}


def _linked_service_navigate(
    service_name: str, factory_name: str, subscription_id: str, resource_group: str,
    tenant_id: str, client_id: str, client_secret: str, reason: str, direction: str, confirm_delete: bool,
) -> dict:
    client = _client(tenant_id, client_id, client_secret, subscription_id)

    def apply(definition: dict) -> dict | None:
        linked_service_resource = LinkedServiceResource.deserialize({"properties": definition})
        error = _reject_if_miscased(linked_service_resource, "linked service")
        if error:
            return error
        client.linked_services.create_or_update(resource_group, factory_name, service_name, linked_service_resource)
        return None

    return _navigate(
        "linkedservice", factory_name, service_name, "service_name", direction, reason, confirm_delete,
        delete_fn=lambda: client.linked_services.delete(resource_group, factory_name, service_name),
        apply_fn=apply,
    )


def back_linked_service_definition(
    service_name: str, factory_name: str,
    subscription_id: str, resource_group: str,
    tenant_id: str, client_id: str, client_secret: str,
    reason: str,
    confirm_delete: bool = False,
) -> dict:
    """Steps one checkpoint back through this linked service's history — see back_pipeline_definition."""
    return _linked_service_navigate(
        service_name, factory_name, subscription_id, resource_group,
        tenant_id, client_id, client_secret, reason, "back", confirm_delete,
    )


def forward_linked_service_definition(
    service_name: str, factory_name: str,
    subscription_id: str, resource_group: str,
    tenant_id: str, client_id: str, client_secret: str,
    reason: str,
    confirm_delete: bool = False,
) -> dict:
    """Steps one checkpoint forward through this linked service's history — see forward_pipeline_definition."""
    return _linked_service_navigate(
        service_name, factory_name, subscription_id, resource_group,
        tenant_id, client_id, client_secret, reason, "forward", confirm_delete,
    )


def get_integration_runtime_status(
    integration_runtime_name: str, factory_name: str,
    subscription_id: str, resource_group: str,
    tenant_id: str, client_id: str, client_secret: str,
) -> dict:
    """Read-only. Works for any integration runtime type (Azure, self-hosted, Azure-SSIS)."""
    client = _client(tenant_id, client_id, client_secret, subscription_id)
    status = client.integration_runtimes.get_status(resource_group, factory_name, integration_runtime_name)
    props = status.properties
    return {
        "name": integration_runtime_name,
        "type": getattr(props, "type", None),
        "state": getattr(props, "state", None),
    }


def start_integration_runtime(
    integration_runtime_name: str, factory_name: str,
    subscription_id: str, resource_group: str,
    tenant_id: str, client_id: str, client_secret: str,
    reason: str,
) -> dict:
    """
    Starts a stopped Azure-SSIS (ManagedReserved) integration runtime.

    Does NOT apply to self-hosted integration runtimes: a self-hosted IR is a Windows
    service on customer infrastructure with no remote-start API anywhere in this SDK.
    If get_integration_runtime_status shows a self-hosted IR as Offline/Limited, that is
    a human-only fix (someone must restart the on-prem service) — do not call this tool
    for that case, it will fail against the service.
    """
    client = _client(tenant_id, client_id, client_secret, subscription_id)
    poller = client.integration_runtimes.begin_start(resource_group, factory_name, integration_runtime_name)
    result = poller.result()
    return {"name": integration_runtime_name, "reason": reason, "state": getattr(result.properties, "state", None)}


def get_trigger(
    trigger_name: str, factory_name: str,
    subscription_id: str, resource_group: str,
    tenant_id: str, client_id: str, client_secret: str,
) -> dict:
    """Read-only. Reports whether a trigger is Started/Stopped/Disabled."""
    client = _client(tenant_id, client_id, client_secret, subscription_id)
    trigger = client.triggers.get(resource_group, factory_name, trigger_name)
    if trigger is None or trigger.properties is None:
        return {"name": trigger_name, "runtime_state": "unknown"}
    return {
        "name": trigger_name,
        "type": trigger.properties.type,
        "runtime_state": trigger.properties.runtime_state,
    }


def start_trigger(
    trigger_name: str, factory_name: str,
    subscription_id: str, resource_group: str,
    tenant_id: str, client_id: str, client_secret: str,
    reason: str,
) -> dict:
    """Starts a stopped/disabled trigger."""
    client = _client(tenant_id, client_id, client_secret, subscription_id)
    poller = client.triggers.begin_start(resource_group, factory_name, trigger_name)
    poller.result()
    trigger = client.triggers.get(resource_group, factory_name, trigger_name)
    runtime_state = trigger.properties.runtime_state if trigger and trigger.properties else None
    return {"name": trigger_name, "reason": reason, "runtime_state": runtime_state}


def stop_trigger(
    trigger_name: str, factory_name: str,
    subscription_id: str, resource_group: str,
    tenant_id: str, client_id: str, client_secret: str,
    reason: str,
) -> dict:
    """
    Stops a running trigger. Inverse of start_trigger — needed if a trigger is misfiring
    after a fix and needs to be paused before it does more damage.
    """
    client = _client(tenant_id, client_id, client_secret, subscription_id)
    poller = client.triggers.begin_stop(resource_group, factory_name, trigger_name)
    poller.result()
    trigger = client.triggers.get(resource_group, factory_name, trigger_name)
    runtime_state = trigger.properties.runtime_state if trigger and trigger.properties else None
    return {"name": trigger_name, "reason": reason, "runtime_state": runtime_state}


def list_triggers(
    factory_name: str,
    subscription_id: str, resource_group: str,
    tenant_id: str, client_id: str, client_secret: str,
) -> dict:
    """Factory-wide trigger sweep — every trigger's name and runtime state in one call."""
    client = _client(tenant_id, client_id, client_secret, subscription_id)
    triggers = client.triggers.list_by_factory(resource_group, factory_name)
    return {
        "triggers": [
            {
                "name": t.name,
                "type": getattr(t.properties, "type", None),
                "runtime_state": getattr(t.properties, "runtime_state", None),
            }
            for t in triggers
        ]
    }


def get_trigger_run_history(
    trigger_name: str, factory_name: str,
    subscription_id: str, resource_group: str,
    tenant_id: str, client_id: str, client_secret: str,
    days: int = 7,
) -> dict:
    """
    Trigger-run history — distinct from pipeline-run history. Needed for tumbling-window
    and event triggers, where the trigger run (not the pipeline run) is the unit that can
    fail, be rerun, or be cancelled independently of the pipeline it invokes.
    """
    client = _client(tenant_id, client_id, client_secret, subscription_id)
    now = datetime.now(timezone.utc)
    run_filter = RunFilterParameters(
        last_updated_after=now - timedelta(days=days),
        last_updated_before=now,
    )
    runs = client.trigger_runs.query_by_factory(resource_group, factory_name, run_filter)
    return {
        "runs": [
            {
                "trigger_run_id": r.trigger_run_id,
                "trigger_name": r.trigger_name,
                "status": r.status,
                "message": r.message,
                "timestamp": str(r.trigger_run_timestamp),
            }
            for r in runs.value
            if r.trigger_name == trigger_name
        ]
    }


def rerun_trigger_run(
    trigger_name: str, trigger_run_id: str, factory_name: str,
    subscription_id: str, resource_group: str,
    tenant_id: str, client_id: str, client_secret: str,
    reason: str,
) -> dict:
    """
    Reruns a specific trigger run. Needed for tumbling-window/event triggers that don't
    go through pipelines.create_run — rerun_pipeline can't retry these.
    """
    client = _client(tenant_id, client_id, client_secret, subscription_id)
    client.trigger_runs.rerun(resource_group, factory_name, trigger_name, trigger_run_id)
    return {"trigger_name": trigger_name, "trigger_run_id": trigger_run_id, "reran": True, "reason": reason}


def cancel_trigger_run(
    trigger_name: str, trigger_run_id: str, factory_name: str,
    subscription_id: str, resource_group: str,
    tenant_id: str, client_id: str, client_secret: str,
    reason: str,
) -> dict:
    """Cancels a specific in-progress trigger run (tumbling-window/event triggers)."""
    client = _client(tenant_id, client_id, client_secret, subscription_id)
    client.trigger_runs.cancel(resource_group, factory_name, trigger_name, trigger_run_id)
    return {"trigger_name": trigger_name, "trigger_run_id": trigger_run_id, "cancelled": True, "reason": reason}


def _snapshot_dir(kind: str, factory_name: str, resource_name: str) -> Path:
    """One directory per resource, grouped by kind first (all pipelines together, etc)."""
    d = _SNAPSHOT_DIR / kind / f"{factory_name}__{resource_name}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _blob_dir(kind: str, factory_name: str, resource_name: str) -> Path:
    d = _snapshot_dir(kind, factory_name, resource_name) / "_blobs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _hash_definition(definition: dict | None) -> str | None:
    """
    Short content hash, like a git blob id — identical content always hashes the same, so
    re-visiting an earlier definition (e.g. A -> B -> A) reuses the existing blob instead
    of storing a duplicate copy. None (the action="create" case: resource didn't exist) has
    no content to hash.
    """
    if definition is None:
        return None
    canonical = json.dumps(definition, sort_keys=True).encode()
    return hashlib.sha256(canonical).hexdigest()[:16]


def _write_blob(kind: str, factory_name: str, resource_name: str, definition: dict | None) -> str | None:
    digest = _hash_definition(definition)
    if digest is None:
        return None
    path = _blob_dir(kind, factory_name, resource_name) / f"{digest}.json"
    if not path.exists():
        path.write_text(json.dumps(definition))
    return digest


def _read_blob(kind: str, factory_name: str, resource_name: str, digest: str | None) -> dict | None:
    if digest is None:
        return None
    return json.loads((_blob_dir(kind, factory_name, resource_name) / f"{digest}.json").read_text())


def _index_path(kind: str, factory_name: str, resource_name: str) -> Path:
    return _snapshot_dir(kind, factory_name, resource_name) / "_index.jsonl"


def _cursor_path(kind: str, factory_name: str, resource_name: str) -> Path:
    return _snapshot_dir(kind, factory_name, resource_name) / "_cursor.json"


def _read_cursor(kind: str, factory_name: str, resource_name: str, entries: list[dict]) -> int:
    """
    Sequence number the live Azure resource currently matches. Defaults to the newest
    entry if no cursor has been written yet (resources with history predating this file).
    """
    p = _cursor_path(kind, factory_name, resource_name)
    if p.exists():
        return json.loads(p.read_text())["sequence"]
    return entries[-1]["sequence"]


def _write_cursor(kind: str, factory_name: str, resource_name: str, sequence: int) -> None:
    _cursor_path(kind, factory_name, resource_name).write_text(json.dumps({"sequence": sequence}))


def _slugify(text: str, max_len: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:max_len].strip("-") or "state"


def _push_snapshot(
    kind: str, factory_name: str, resource_name: str, *,
    definition: dict | None, reason: str, change_summary: str | None = None,
    state_name: str | None = None, action: str = "exists",
) -> dict:
    """
    Appends one line to this resource's index — a timestamped pointer at the content's
    hash, like a git commit pointing at a tree. Identical content (e.g. re-visiting an
    earlier definition) reuses the existing blob instead of storing it again. Nothing in
    the index is ever rewritten or removed, so every checkpoint stays queryable
    (list_*_snapshots) and re-visitable (rollback/back/forward) indefinitely.
    `action="create"` is the one special case: it means "the resource did not exist at
    this point" (definition=None, no blob) — moving to it means delete, not restore.
    """
    index_path = _index_path(kind, factory_name, resource_name)
    sequence = (sum(1 for _ in index_path.open("r", encoding="utf-8")) if index_path.exists() else 0) + 1
    digest = _write_blob(kind, factory_name, resource_name, definition)
    entry = {
        "sequence": sequence,
        "state_name": state_name or _slugify(change_summary or reason),
        "action": action,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "reason": reason,
        "change_summary": change_summary,
        "hash": digest,
    }
    with index_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    # A genuine create/update always becomes the new "current" state, regardless of
    # where the cursor was left by a previous back_*/forward_* call.
    _write_cursor(kind, factory_name, resource_name, sequence)
    return {**entry, "definition": definition}


def _ensure_baseline(kind: str, factory_name: str, resource_name: str, current_definition: dict, reason: str) -> None:
    """
    If this resource has no history yet (e.g. it already existed before any create_pipeline/
    update_*_definition call ever touched it), captures its as-found content as the first
    checkpoint ("initial") before any change is applied — otherwise that original state
    would never be nameable or reachable again once the first update overwrites it.
    """
    if not _list_snapshots(kind, factory_name, resource_name):
        _push_snapshot(
            kind, factory_name, resource_name, definition=current_definition, reason=reason,
            change_summary="captured as first-seen state", state_name="initial",
        )


def _list_snapshots(kind: str, factory_name: str, resource_name: str) -> list[dict]:
    """Metadata only (no `definition` blob — that's a separate _read_blob call) for every
    checkpoint, oldest first."""
    index_path = _index_path(kind, factory_name, resource_name)
    if not index_path.exists():
        return []
    with index_path.open("r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _find_snapshot(kind: str, factory_name: str, resource_name: str, state_name: str) -> dict | None:
    """Newest-first search, so a re-used name resolves to its most recent occurrence."""
    for entry in reversed(_list_snapshots(kind, factory_name, resource_name)):
        if entry["state_name"] == state_name:
            return {**entry, "definition": _read_blob(kind, factory_name, resource_name, entry["hash"])}
    return None


def _step_snapshot(kind: str, factory_name: str, resource_name: str, direction: str) -> dict:
    """
    Moves the resource's cursor one step through its existing history — like
    `git checkout HEAD~1` / `git checkout HEAD@{1}` — without appending anything to the
    index. `direction` is "back" or "forward". Returns either {"error": ...} or the full
    snapshot entry (incl. `definition`) the caller should now apply to the live resource.
    Callers must call _write_cursor(..., entry["sequence"]) themselves once they've
    actually applied the change (so a no-op preview never moves the cursor).
    """
    entries = _list_snapshots(kind, factory_name, resource_name)
    if not entries:
        return {"error": "no_history"}
    seqs = [e["sequence"] for e in entries]
    cursor = _read_cursor(kind, factory_name, resource_name, entries)
    idx = seqs.index(cursor) if cursor in seqs else len(seqs) - 1
    new_idx = idx - 1 if direction == "back" else idx + 1
    if new_idx < 0:
        return {"error": "no_earlier_state_available"}
    if new_idx >= len(entries):
        return {"error": "no_later_state_available"}
    target = entries[new_idx]
    return {**target, "definition": _read_blob(kind, factory_name, resource_name, target["hash"])}


def _navigate(
    kind: str, factory_name: str, resource_name: str, name_key: str,
    direction: str, reason: str, confirm_delete: bool,
    delete_fn: Callable[[], None], apply_fn: Callable[[dict], dict | None],
) -> dict:
    """
    Shared back_*/forward_* engine for all four resource kinds. `delete_fn` deletes the
    live resource (used when the target checkpoint predates its creation); `apply_fn`
    pushes the target's stored definition to the live resource and returns an error dict
    on failure, None on success. Callers supply these as closures so this stays kind-
    agnostic; see _step_snapshot for how the cursor moves without touching the index.
    """
    target = _step_snapshot(kind, factory_name, resource_name, direction)
    if "error" in target:
        return {**target, name_key: resource_name}

    if target["action"] == "create":
        if not confirm_delete:
            return {
                "requires_confirmation": True,
                "would": "delete",
                name_key: resource_name,
                "target_state": target["state_name"],
                "message": (
                    f"Stepping {direction} to '{target['state_name']}' means this {kind} did not "
                    "exist yet at that point — proceeding will DELETE the live resource in Azure. "
                    "Ask the human before proceeding; call again with confirm_delete=true only "
                    "after they agree."
                ),
            }
        delete_fn()
        _write_cursor(kind, factory_name, resource_name, target["sequence"])
        return {name_key: resource_name, "moved_to": target["state_name"], "deleted": True, "reason": reason}

    error = apply_fn(target["definition"])
    if error:
        return error
    _write_cursor(kind, factory_name, resource_name, target["sequence"])
    return {name_key: resource_name, "moved_to": target["state_name"], "reason": reason}


def get_pipeline_definition_raw(
    pipeline_name: str, factory_name: str,
    subscription_id: str, resource_group: str,
    tenant_id: str, client_id: str, client_secret: str,
) -> dict:
    """
    Full pipeline definition — every activity's typeProperties (dataset/linked-service
    references via inputs/outputs/linkedServiceName, queries like sqlReaderQuery, source/sink
    settings), policy (timeout/retry), and dependsOn.

    Use during diagnosis, not just before writing a fix: after get_activity_run_error
    identifies the failing activity, call this to see its actual timeout value, the query
    it ran, or which dataset/linked service it touches — usually the concrete evidence for
    WHY it failed. Also the editable structure to feed back into update_pipeline_definition
    (with your modifications applied) once a fix is decided.
    """
    client = _client(tenant_id, client_id, client_secret, subscription_id)
    pipeline = client.pipelines.get(resource_group, factory_name, pipeline_name)
    return _to_wire_dict(pipeline)


def update_pipeline_definition(
    pipeline_name: str, factory_name: str,
    subscription_id: str, resource_group: str,
    tenant_id: str, client_id: str, client_secret: str,
    definition: dict,
    reason: str,
    change_summary: str,
    state_name: str | None = None,
) -> dict:
    """
    Overwrites the pipeline's full definition (ADF has no "patch one activity" API —
    create_or_update replaces the whole activities array). If this pipeline has no history
    yet, its as-found content is captured as an "initial" checkpoint first. After applying
    the change, the NEW (resulting) content is pushed as a named checkpoint — so
    `state_name` (or its default, a slug of `change_summary`) always names the state you're
    moving TO, and rollback_pipeline_definition(state_name=<that name>) restores exactly
    this resulting content, not what came before it.

    `definition` should be get_pipeline_definition_raw's output with your edits applied
    (e.g. a new Wait activity inserted with dependsOn rewired to the correct predecessor).
    """
    client = _client(tenant_id, client_id, client_secret, subscription_id)
    current = client.pipelines.get(resource_group, factory_name, pipeline_name)
    _ensure_baseline("pipeline", factory_name, pipeline_name, _to_wire_dict(current), reason)

    pipeline_resource = PipelineResource.deserialize(definition)
    error = _reject_if_miscased(pipeline_resource, "pipeline")
    if error:
        return error
    updated = client.pipelines.create_or_update(resource_group, factory_name, pipeline_name, pipeline_resource)

    saved = _push_snapshot(
        "pipeline", factory_name, pipeline_name,
        definition=_to_wire_dict(updated), reason=reason, change_summary=change_summary,
        state_name=state_name,
    )
    return {
        "pipeline_name": pipeline_name,
        "updated": True,
        "reason": reason,
        "change_summary": change_summary,
        "saved_state_name": saved["state_name"],
        "etag": updated.etag,
    }


def list_pipeline_snapshots(
    pipeline_name: str, factory_name: str,
    subscription_id: str, resource_group: str,
    tenant_id: str, client_id: str, client_secret: str,
) -> dict:
    """
    Lists every named checkpoint saved for this pipeline (state_name, timestamp, reason,
    change_summary), oldest first. Query this to see what's available before calling
    rollback_pipeline_definition with a specific state_name.
    """
    return {"pipeline_name": pipeline_name, "states": _list_snapshots("pipeline", factory_name, pipeline_name)}


def rollback_pipeline_definition(
    pipeline_name: str, factory_name: str,
    subscription_id: str, resource_group: str,
    tenant_id: str, client_id: str, client_secret: str,
    reason: str,
    state_name: str,
    confirm_delete: bool = False,
) -> dict:
    """
    Jumps directly to any named checkpoint from list_pipeline_snapshots, regardless of
    where it sits in history — nothing is ever deleted. For simple one-step undo/redo
    without needing a state_name, use back_pipeline_definition / forward_pipeline_definition
    instead.

    If the target checkpoint predates the pipeline's creation, applying it means DELETING
    the live pipeline — this returns {"requires_confirmation": true} instead of deleting;
    call again with confirm_delete=true only after the human has agreed to that.
    """
    client = _client(tenant_id, client_id, client_secret, subscription_id)

    target = _find_snapshot("pipeline", factory_name, pipeline_name, state_name)
    if target is None:
        return {"error": "state_not_found", "pipeline_name": pipeline_name, "state_name": state_name}

    if target["action"] == "create":
        if not confirm_delete:
            return {
                "requires_confirmation": True,
                "would": "delete",
                "pipeline_name": pipeline_name,
                "target_state": target["state_name"],
                "message": (
                    f"'{target['state_name']}' predates this pipeline's creation — rolling back to it "
                    "means DELETING the live pipeline in Azure. Ask the human before proceeding; "
                    "call again with confirm_delete=true only after they agree."
                ),
            }
        client.pipelines.delete(resource_group, factory_name, pipeline_name)
        _push_snapshot(
            "pipeline", factory_name, pipeline_name,
            definition=None, reason=reason, change_summary=f"rolled back to '{target['state_name']}'",
            state_name=target["state_name"], action="create",
        )
        return {"pipeline_name": pipeline_name, "rolled_back_to": target["state_name"], "deleted": True, "reason": reason}

    pipeline_resource = PipelineResource.deserialize(target["definition"])
    error = _reject_if_miscased(pipeline_resource, "pipeline")
    if error:
        return error
    updated = client.pipelines.create_or_update(resource_group, factory_name, pipeline_name, pipeline_resource)
    _push_snapshot(
        "pipeline", factory_name, pipeline_name,
        definition=_to_wire_dict(updated), reason=reason, change_summary=f"rolled back to '{target['state_name']}'",
        state_name=target["state_name"],
    )
    return {"pipeline_name": pipeline_name, "rolled_back_to": target["state_name"], "reason": reason}


def _pipeline_navigate(
    pipeline_name: str, factory_name: str, subscription_id: str, resource_group: str,
    tenant_id: str, client_id: str, client_secret: str, reason: str, direction: str, confirm_delete: bool,
) -> dict:
    client = _client(tenant_id, client_id, client_secret, subscription_id)

    def apply(definition: dict) -> dict | None:
        pipeline_resource = PipelineResource.deserialize(definition)
        error = _reject_if_miscased(pipeline_resource, "pipeline")
        if error:
            return error
        client.pipelines.create_or_update(resource_group, factory_name, pipeline_name, pipeline_resource)
        return None

    return _navigate(
        "pipeline", factory_name, pipeline_name, "pipeline_name", direction, reason, confirm_delete,
        delete_fn=lambda: client.pipelines.delete(resource_group, factory_name, pipeline_name),
        apply_fn=apply,
    )


def back_pipeline_definition(
    pipeline_name: str, factory_name: str,
    subscription_id: str, resource_group: str,
    tenant_id: str, client_id: str, client_secret: str,
    reason: str,
    confirm_delete: bool = False,
) -> dict:
    """
    Moves one step back through this pipeline's history, like `git checkout HEAD~1` —
    the history log itself is untouched, only which checkpoint the live pipeline currently
    matches moves. Call forward_pipeline_definition to step forward again afterwards;
    repeated back/forward calls walk the full log correctly in either direction, and
    nothing is ever lost by moving.

    If the step back lands on a point before this pipeline existed, applying it means
    DELETING the live pipeline — this returns {"requires_confirmation": true} instead of
    deleting; call again with confirm_delete=true only after the human has agreed to that.
    """
    return _pipeline_navigate(
        pipeline_name, factory_name, subscription_id, resource_group,
        tenant_id, client_id, client_secret, reason, "back", confirm_delete,
    )


def forward_pipeline_definition(
    pipeline_name: str, factory_name: str,
    subscription_id: str, resource_group: str,
    tenant_id: str, client_id: str, client_secret: str,
    reason: str,
    confirm_delete: bool = False,
) -> dict:
    """
    Moves one step forward through this pipeline's history, like `git checkout HEAD@{1}` —
    the mirror of back_pipeline_definition. Only available after a previous back step;
    returns {"error": "no_later_state_available"} if the cursor is already at the newest
    checkpoint. See back_pipeline_definition for the confirm_delete behavior.
    """
    return _pipeline_navigate(
        pipeline_name, factory_name, subscription_id, resource_group,
        tenant_id, client_id, client_secret, reason, "forward", confirm_delete,
    )


def cancel_pipeline_run(
    run_id: str, factory_name: str,
    subscription_id: str, resource_group: str,
    tenant_id: str, client_id: str, client_secret: str,
    reason: str,
) -> dict:
    """
    Cancels a running (or hung) pipeline run. Needed before retrying a fix if a prior
    rerun_pipeline call is stuck rather than cleanly failed — otherwise runs pile up.
    """
    client = _client(tenant_id, client_id, client_secret, subscription_id)
    client.pipeline_runs.cancel(resource_group, factory_name, run_id, is_recursive=True)
    return {"run_id": run_id, "cancelled": True, "reason": reason}


def rerun_pipeline(
    pipeline_name: str, factory_name: str,
    subscription_id: str, resource_group: str,
    tenant_id: str, client_id: str, client_secret: str,
    reason: str,
    parameters: dict | None = None,
) -> dict:
    """Triggers a new run of the pipeline, optionally with override parameters."""
    client = _client(tenant_id, client_id, client_secret, subscription_id)
    run = client.pipelines.create_run(
        resource_group, factory_name, pipeline_name,
        parameters=parameters or {},
    )
    return {"new_run_id": run.run_id, "reason": reason}


def list_datasets(
    factory_name: str,
    subscription_id: str, resource_group: str,
    tenant_id: str, client_id: str, client_secret: str,
) -> dict:
    """Factory-wide dataset sweep — name, type, and backing linked service for each."""
    client = _client(tenant_id, client_id, client_secret, subscription_id)
    datasets = client.datasets.list_by_factory(resource_group, factory_name)
    return {
        "datasets": [
            {
                "name": d.name,
                "type": d.properties.type if d.properties else "unknown",
                "linked_service_name": (
                    getattr(d.properties.linked_service_name, "reference_name", None)
                    if d.properties and d.properties.linked_service_name else None
                ),
            }
            for d in datasets
        ]
    }


def get_dataset_definition_raw(
    dataset_name: str, factory_name: str,
    subscription_id: str, resource_group: str,
    tenant_id: str, client_id: str, client_secret: str,
) -> dict:
    """
    Full dataset definition (schema, structure, linked service reference, parameters) —
    the evidence needed to diagnose `schema_drift` (source/sink no longer matches the
    dataset's declared schema). Feed the returned dict back into update_dataset_definition
    (with edits applied) to apply a fix.
    """
    client = _client(tenant_id, client_id, client_secret, subscription_id)
    dataset = client.datasets.get(resource_group, factory_name, dataset_name)
    return _to_wire_dict(dataset)


def update_dataset_definition(
    dataset_name: str, factory_name: str,
    subscription_id: str, resource_group: str,
    tenant_id: str, client_id: str, client_secret: str,
    definition: dict,
    reason: str,
    change_summary: str,
    state_name: str | None = None,
) -> dict:
    """
    Overwrites a dataset's full definition (e.g. to correct a drifted schema). If this
    dataset has no history yet, its as-found content is captured as an "initial" checkpoint
    first. After applying the change, the NEW content is pushed as a named checkpoint — see
    update_pipeline_definition for the full history design. `definition` should be
    get_dataset_definition_raw's output with edits applied.
    """
    client = _client(tenant_id, client_id, client_secret, subscription_id)
    current = client.datasets.get(resource_group, factory_name, dataset_name)
    _ensure_baseline("dataset", factory_name, dataset_name, _to_wire_dict(current), reason)

    dataset_resource = DatasetResource.deserialize(definition)
    error = _reject_if_miscased(dataset_resource, "dataset")
    if error:
        return error
    updated = client.datasets.create_or_update(resource_group, factory_name, dataset_name, dataset_resource)

    saved = _push_snapshot(
        "dataset", factory_name, dataset_name,
        definition=_to_wire_dict(updated), reason=reason, change_summary=change_summary,
        state_name=state_name,
    )
    return {
        "dataset_name": dataset_name,
        "updated": True,
        "reason": reason,
        "change_summary": change_summary,
        "saved_state_name": saved["state_name"],
        "etag": updated.etag,
    }


def list_dataset_snapshots(
    dataset_name: str, factory_name: str,
    subscription_id: str, resource_group: str,
    tenant_id: str, client_id: str, client_secret: str,
) -> dict:
    """Lists every named checkpoint saved for this dataset, oldest first."""
    return {"dataset_name": dataset_name, "states": _list_snapshots("dataset", factory_name, dataset_name)}


def rollback_dataset_definition(
    dataset_name: str, factory_name: str,
    subscription_id: str, resource_group: str,
    tenant_id: str, client_id: str, client_secret: str,
    reason: str,
    state_name: str,
    confirm_delete: bool = False,
) -> dict:
    """
    Jumps directly to any named checkpoint from list_dataset_snapshots, regardless of
    where it sits in history — nothing is ever deleted. For simple one-step undo/redo
    without needing a state_name, use back_dataset_definition / forward_dataset_definition
    instead.

    If the target checkpoint predates the dataset's creation, applying it means DELETING
    it — this returns {"requires_confirmation": true} instead of deleting; call again
    with confirm_delete=true only after the human has agreed to that.
    """
    client = _client(tenant_id, client_id, client_secret, subscription_id)

    target = _find_snapshot("dataset", factory_name, dataset_name, state_name)
    if target is None:
        return {"error": "state_not_found", "dataset_name": dataset_name, "state_name": state_name}

    if target["action"] == "create":
        if not confirm_delete:
            return {
                "requires_confirmation": True,
                "would": "delete",
                "dataset_name": dataset_name,
                "target_state": target["state_name"],
                "message": (
                    f"'{target['state_name']}' predates this dataset's creation — rolling back to "
                    "it means DELETING it in Azure. Ask the human before proceeding; call again "
                    "with confirm_delete=true only after they agree."
                ),
            }
        client.datasets.delete(resource_group, factory_name, dataset_name)
        _push_snapshot(
            "dataset", factory_name, dataset_name,
            definition=None, reason=reason, change_summary=f"rolled back to '{target['state_name']}'",
            state_name=target["state_name"], action="create",
        )
        return {"dataset_name": dataset_name, "rolled_back_to": target["state_name"], "deleted": True, "reason": reason}

    dataset_resource = DatasetResource.deserialize(target["definition"])
    error = _reject_if_miscased(dataset_resource, "dataset")
    if error:
        return error
    updated = client.datasets.create_or_update(resource_group, factory_name, dataset_name, dataset_resource)
    _push_snapshot(
        "dataset", factory_name, dataset_name,
        definition=_to_wire_dict(updated), reason=reason, change_summary=f"rolled back to '{target['state_name']}'",
        state_name=target["state_name"],
    )
    return {"dataset_name": dataset_name, "rolled_back_to": target["state_name"], "reason": reason}


def _dataset_navigate(
    dataset_name: str, factory_name: str, subscription_id: str, resource_group: str,
    tenant_id: str, client_id: str, client_secret: str, reason: str, direction: str, confirm_delete: bool,
) -> dict:
    client = _client(tenant_id, client_id, client_secret, subscription_id)

    def apply(definition: dict) -> dict | None:
        dataset_resource = DatasetResource.deserialize(definition)
        error = _reject_if_miscased(dataset_resource, "dataset")
        if error:
            return error
        client.datasets.create_or_update(resource_group, factory_name, dataset_name, dataset_resource)
        return None

    return _navigate(
        "dataset", factory_name, dataset_name, "dataset_name", direction, reason, confirm_delete,
        delete_fn=lambda: client.datasets.delete(resource_group, factory_name, dataset_name),
        apply_fn=apply,
    )


def back_dataset_definition(
    dataset_name: str, factory_name: str,
    subscription_id: str, resource_group: str,
    tenant_id: str, client_id: str, client_secret: str,
    reason: str,
    confirm_delete: bool = False,
) -> dict:
    """Steps one checkpoint back through this dataset's history — see back_pipeline_definition."""
    return _dataset_navigate(
        dataset_name, factory_name, subscription_id, resource_group,
        tenant_id, client_id, client_secret, reason, "back", confirm_delete,
    )


def forward_dataset_definition(
    dataset_name: str, factory_name: str,
    subscription_id: str, resource_group: str,
    tenant_id: str, client_id: str, client_secret: str,
    reason: str,
    confirm_delete: bool = False,
) -> dict:
    """Steps one checkpoint forward through this dataset's history — see forward_pipeline_definition."""
    return _dataset_navigate(
        dataset_name, factory_name, subscription_id, resource_group,
        tenant_id, client_id, client_secret, reason, "forward", confirm_delete,
    )


def get_data_flow_definition(
    data_flow_name: str, factory_name: str,
    subscription_id: str, resource_group: str,
    tenant_id: str, client_id: str, client_secret: str,
) -> dict:
    """
    Full Mapping Data Flow definition (sources, sinks, transformation script). Pipeline
    definition tools only show that an activity references a data flow by name — this is
    the only way to see (and diagnose `schema_drift`/`business_logic` failures inside) the
    transformation graph itself. Feed the returned dict back into
    update_data_flow_definition (with edits applied) to apply a fix.
    """
    client = _client(tenant_id, client_id, client_secret, subscription_id)
    data_flow = client.data_flows.get(resource_group, factory_name, data_flow_name)
    return _to_wire_dict(data_flow)


def update_data_flow_definition(
    data_flow_name: str, factory_name: str,
    subscription_id: str, resource_group: str,
    tenant_id: str, client_id: str, client_secret: str,
    definition: dict,
    reason: str,
    change_summary: str,
    state_name: str | None = None,
) -> dict:
    """
    Overwrites a data flow's full definition. If this data flow has no history yet, its
    as-found content is captured as an "initial" checkpoint first. After applying the
    change, the NEW content is pushed as a named checkpoint — see
    update_pipeline_definition for the full history design. `definition` should be
    get_data_flow_definition's output with edits applied.
    """
    client = _client(tenant_id, client_id, client_secret, subscription_id)
    current = client.data_flows.get(resource_group, factory_name, data_flow_name)
    _ensure_baseline("dataflow", factory_name, data_flow_name, _to_wire_dict(current), reason)

    data_flow_resource = DataFlowResource.deserialize(definition)
    error = _reject_if_miscased(data_flow_resource, "data flow")
    if error:
        return error
    updated = client.data_flows.create_or_update(resource_group, factory_name, data_flow_name, data_flow_resource)

    saved = _push_snapshot(
        "dataflow", factory_name, data_flow_name,
        definition=_to_wire_dict(updated), reason=reason, change_summary=change_summary,
        state_name=state_name,
    )
    return {
        "data_flow_name": data_flow_name,
        "updated": True,
        "reason": reason,
        "change_summary": change_summary,
        "saved_state_name": saved["state_name"],
        "etag": updated.etag,
    }


def list_data_flow_snapshots(
    data_flow_name: str, factory_name: str,
    subscription_id: str, resource_group: str,
    tenant_id: str, client_id: str, client_secret: str,
) -> dict:
    """Lists every named checkpoint saved for this data flow, oldest first."""
    return {"data_flow_name": data_flow_name, "states": _list_snapshots("dataflow", factory_name, data_flow_name)}


def rollback_data_flow_definition(
    data_flow_name: str, factory_name: str,
    subscription_id: str, resource_group: str,
    tenant_id: str, client_id: str, client_secret: str,
    reason: str,
    state_name: str,
    confirm_delete: bool = False,
) -> dict:
    """
    Jumps directly to any named checkpoint from list_data_flow_snapshots, regardless of
    where it sits in history — nothing is ever deleted. For simple one-step undo/redo
    without needing a state_name, use back_data_flow_definition /
    forward_data_flow_definition instead.

    If the target checkpoint predates the data flow's creation, applying it means
    DELETING it — this returns {"requires_confirmation": true} instead of deleting; call
    again with confirm_delete=true only after the human has agreed to that.
    """
    client = _client(tenant_id, client_id, client_secret, subscription_id)

    target = _find_snapshot("dataflow", factory_name, data_flow_name, state_name)
    if target is None:
        return {"error": "state_not_found", "data_flow_name": data_flow_name, "state_name": state_name}

    if target["action"] == "create":
        if not confirm_delete:
            return {
                "requires_confirmation": True,
                "would": "delete",
                "data_flow_name": data_flow_name,
                "target_state": target["state_name"],
                "message": (
                    f"'{target['state_name']}' predates this data flow's creation — rolling back to "
                    "it means DELETING it in Azure. Ask the human before proceeding; call again "
                    "with confirm_delete=true only after they agree."
                ),
            }
        client.data_flows.delete(resource_group, factory_name, data_flow_name)
        _push_snapshot(
            "dataflow", factory_name, data_flow_name,
            definition=None, reason=reason, change_summary=f"rolled back to '{target['state_name']}'",
            state_name=target["state_name"], action="create",
        )
        return {"data_flow_name": data_flow_name, "rolled_back_to": target["state_name"], "deleted": True, "reason": reason}

    data_flow_resource = DataFlowResource.deserialize(target["definition"])
    error = _reject_if_miscased(data_flow_resource, "data flow")
    if error:
        return error
    updated = client.data_flows.create_or_update(resource_group, factory_name, data_flow_name, data_flow_resource)
    _push_snapshot(
        "dataflow", factory_name, data_flow_name,
        definition=_to_wire_dict(updated), reason=reason, change_summary=f"rolled back to '{target['state_name']}'",
        state_name=target["state_name"],
    )
    return {"data_flow_name": data_flow_name, "rolled_back_to": target["state_name"], "reason": reason}


def _data_flow_navigate(
    data_flow_name: str, factory_name: str, subscription_id: str, resource_group: str,
    tenant_id: str, client_id: str, client_secret: str, reason: str, direction: str, confirm_delete: bool,
) -> dict:
    client = _client(tenant_id, client_id, client_secret, subscription_id)

    def apply(definition: dict) -> dict | None:
        data_flow_resource = DataFlowResource.deserialize(definition)
        error = _reject_if_miscased(data_flow_resource, "data flow")
        if error:
            return error
        client.data_flows.create_or_update(resource_group, factory_name, data_flow_name, data_flow_resource)
        return None

    return _navigate(
        "dataflow", factory_name, data_flow_name, "data_flow_name", direction, reason, confirm_delete,
        delete_fn=lambda: client.data_flows.delete(resource_group, factory_name, data_flow_name),
        apply_fn=apply,
    )


def back_data_flow_definition(
    data_flow_name: str, factory_name: str,
    subscription_id: str, resource_group: str,
    tenant_id: str, client_id: str, client_secret: str,
    reason: str,
    confirm_delete: bool = False,
) -> dict:
    """Steps one checkpoint back through this data flow's history — see back_pipeline_definition."""
    return _data_flow_navigate(
        data_flow_name, factory_name, subscription_id, resource_group,
        tenant_id, client_id, client_secret, reason, "back", confirm_delete,
    )


def forward_data_flow_definition(
    data_flow_name: str, factory_name: str,
    subscription_id: str, resource_group: str,
    tenant_id: str, client_id: str, client_secret: str,
    reason: str,
    confirm_delete: bool = False,
) -> dict:
    """Steps one checkpoint forward through this data flow's history — see forward_pipeline_definition."""
    return _data_flow_navigate(
        data_flow_name, factory_name, subscription_id, resource_group,
        tenant_id, client_id, client_secret, reason, "forward", confirm_delete,
    )


def list_global_parameters(
    factory_name: str,
    subscription_id: str, resource_group: str,
    tenant_id: str, client_id: str, client_secret: str,
) -> dict:
    """
    Factory-wide global parameter sweep — name, type, and value for each. These are the
    factory-level parameters referenced by pipelines/datasets/linked services via
    @pipeline().globalParameters.<name>.

    NOTE: the ADF SDK models a global parameter's `properties` as a dict keyed by name
    (not a flat object), an unverified-against-a-live-factory quirk of this SDK version —
    this flattens that into a plain list. If a real factory ever returns a shape this
    doesn't expect, treat this tool's output as suspect and check the SDK response directly.
    """
    client = _client(tenant_id, client_id, client_secret, subscription_id)
    items = client.global_parameters.list_by_factory(resource_group, factory_name)
    params = []
    for item in items:
        for name, spec in (item.properties or {}).items():
            params.append({
                "name": name,
                "type": getattr(spec, "type", None),
                "value": getattr(spec, "value", None),
            })
    return {"global_parameters": params}


def get_global_parameter_definition_raw(
    global_parameter_name: str, factory_name: str,
    subscription_id: str, resource_group: str,
    tenant_id: str, client_id: str, client_secret: str,
) -> dict:
    """
    Full global parameter definition ({"type": ..., "value": ...}). Feed the returned dict
    back into update_global_parameter_definition (with edits applied) to apply a fix — e.g.
    a stale connection string or environment flag baked in as a global. See
    list_global_parameters for a note on this SDK's dict-keyed-by-name response shape.
    """
    client = _client(tenant_id, client_id, client_secret, subscription_id)
    param = client.global_parameters.get(resource_group, factory_name, global_parameter_name)
    wire = _to_wire_dict(param)
    return wire.get(global_parameter_name, wire)


def update_global_parameter_definition(
    global_parameter_name: str, factory_name: str,
    subscription_id: str, resource_group: str,
    tenant_id: str, client_id: str, client_secret: str,
    definition: dict,
    reason: str,
    change_summary: str,
    state_name: str | None = None,
) -> dict:
    """
    Overwrites a global parameter's type/value. If this parameter has no history yet, its
    as-found content is captured as an "initial" checkpoint first. After applying the
    change, the NEW content is pushed as a named checkpoint — see update_pipeline_definition
    for the full history design. `definition` should be get_global_parameter_definition_raw's
    output ({"type": ..., "value": ...}) with edits applied.
    """
    client = _client(tenant_id, client_id, client_secret, subscription_id)
    current = client.global_parameters.get(resource_group, factory_name, global_parameter_name)
    current_wire = _to_wire_dict(current)
    _ensure_baseline(
        "globalparameter", factory_name, global_parameter_name,
        current_wire.get(global_parameter_name, current_wire), reason,
    )

    global_parameter_resource = GlobalParameterResource.deserialize(
        {"properties": {global_parameter_name: definition}}
    )
    error = _reject_if_miscased(global_parameter_resource, "global parameter")
    if error:
        return error
    updated = client.global_parameters.create_or_update(
        resource_group, factory_name, global_parameter_name, global_parameter_resource
    )
    updated_wire = _to_wire_dict(updated)

    saved = _push_snapshot(
        "globalparameter", factory_name, global_parameter_name,
        definition=updated_wire.get(global_parameter_name, updated_wire),
        reason=reason, change_summary=change_summary, state_name=state_name,
    )
    return {
        "global_parameter_name": global_parameter_name,
        "updated": True,
        "reason": reason,
        "change_summary": change_summary,
        "saved_state_name": saved["state_name"],
        "etag": updated.etag,
    }


def list_global_parameter_snapshots(
    global_parameter_name: str, factory_name: str,
    subscription_id: str, resource_group: str,
    tenant_id: str, client_id: str, client_secret: str,
) -> dict:
    """Lists every named checkpoint saved for this global parameter, oldest first."""
    return {
        "global_parameter_name": global_parameter_name,
        "states": _list_snapshots("globalparameter", factory_name, global_parameter_name),
    }


def rollback_global_parameter_definition(
    global_parameter_name: str, factory_name: str,
    subscription_id: str, resource_group: str,
    tenant_id: str, client_id: str, client_secret: str,
    reason: str,
    state_name: str,
    confirm_delete: bool = False,
) -> dict:
    """
    Jumps directly to any named checkpoint from list_global_parameter_snapshots, regardless
    of where it sits in history — nothing is ever deleted. For simple one-step undo/redo
    without needing a state_name, use back_global_parameter_definition /
    forward_global_parameter_definition instead.

    If the target checkpoint predates the global parameter's creation, applying it means
    DELETING it — this returns {"requires_confirmation": true} instead of deleting; call
    again with confirm_delete=true only after the human has agreed to that.
    """
    client = _client(tenant_id, client_id, client_secret, subscription_id)

    target = _find_snapshot("globalparameter", factory_name, global_parameter_name, state_name)
    if target is None:
        return {"error": "state_not_found", "global_parameter_name": global_parameter_name, "state_name": state_name}

    if target["action"] == "create":
        if not confirm_delete:
            return {
                "requires_confirmation": True,
                "would": "delete",
                "global_parameter_name": global_parameter_name,
                "target_state": target["state_name"],
                "message": (
                    f"'{target['state_name']}' predates this global parameter's creation — rolling "
                    "back to it means DELETING it in Azure. Ask the human before proceeding; call "
                    "again with confirm_delete=true only after they agree."
                ),
            }
        client.global_parameters.delete(resource_group, factory_name, global_parameter_name)
        _push_snapshot(
            "globalparameter", factory_name, global_parameter_name,
            definition=None, reason=reason, change_summary=f"rolled back to '{target['state_name']}'",
            state_name=target["state_name"], action="create",
        )
        return {
            "global_parameter_name": global_parameter_name, "rolled_back_to": target["state_name"],
            "deleted": True, "reason": reason,
        }

    global_parameter_resource = GlobalParameterResource.deserialize(
        {"properties": {global_parameter_name: target["definition"]}}
    )
    error = _reject_if_miscased(global_parameter_resource, "global parameter")
    if error:
        return error
    updated = client.global_parameters.create_or_update(
        resource_group, factory_name, global_parameter_name, global_parameter_resource
    )
    updated_wire = _to_wire_dict(updated)
    _push_snapshot(
        "globalparameter", factory_name, global_parameter_name,
        definition=updated_wire.get(global_parameter_name, updated_wire), reason=reason,
        change_summary=f"rolled back to '{target['state_name']}'", state_name=target["state_name"],
    )
    return {"global_parameter_name": global_parameter_name, "rolled_back_to": target["state_name"], "reason": reason}


def _global_parameter_navigate(
    global_parameter_name: str, factory_name: str, subscription_id: str, resource_group: str,
    tenant_id: str, client_id: str, client_secret: str, reason: str, direction: str, confirm_delete: bool,
) -> dict:
    client = _client(tenant_id, client_id, client_secret, subscription_id)

    def apply(definition: dict) -> dict | None:
        global_parameter_resource = GlobalParameterResource.deserialize(
            {"properties": {global_parameter_name: definition}}
        )
        error = _reject_if_miscased(global_parameter_resource, "global parameter")
        if error:
            return error
        client.global_parameters.create_or_update(
            resource_group, factory_name, global_parameter_name, global_parameter_resource
        )
        return None

    return _navigate(
        "globalparameter", factory_name, global_parameter_name, "global_parameter_name",
        direction, reason, confirm_delete,
        delete_fn=lambda: client.global_parameters.delete(resource_group, factory_name, global_parameter_name),
        apply_fn=apply,
    )


def back_global_parameter_definition(
    global_parameter_name: str, factory_name: str,
    subscription_id: str, resource_group: str,
    tenant_id: str, client_id: str, client_secret: str,
    reason: str,
    confirm_delete: bool = False,
) -> dict:
    """Steps one checkpoint back through this global parameter's history — see back_pipeline_definition."""
    return _global_parameter_navigate(
        global_parameter_name, factory_name, subscription_id, resource_group,
        tenant_id, client_id, client_secret, reason, "back", confirm_delete,
    )


def forward_global_parameter_definition(
    global_parameter_name: str, factory_name: str,
    subscription_id: str, resource_group: str,
    tenant_id: str, client_id: str, client_secret: str,
    reason: str,
    confirm_delete: bool = False,
) -> dict:
    """Steps one checkpoint forward through this global parameter's history — see forward_pipeline_definition."""
    return _global_parameter_navigate(
        global_parameter_name, factory_name, subscription_id, resource_group,
        tenant_id, client_id, client_secret, reason, "forward", confirm_delete,
    )


# Single source of truth for the ADF tool registry.
# Imported by both server.py (stdio MCP path) and rbac.py (in-process RBAC gateway path).
TOOL_REGISTRY: dict[str, Callable[..., dict]] = {
    "list_pipelines": list_pipelines,
    "create_pipeline": create_pipeline,
    "get_activity_run_error": get_activity_run_error,
    "list_activity_runs": list_activity_runs,
    "get_activity_run_io": get_activity_run_io,
    "get_pipeline_run_status": get_pipeline_run_status,
    "get_pipeline_run_history": get_pipeline_run_history,
    "get_activity_run_history": get_activity_run_history,
    "get_pipeline_definition": get_pipeline_definition,
    "get_linked_service": get_linked_service,
    "rerun_pipeline": rerun_pipeline,
    "get_integration_runtime_status": get_integration_runtime_status,
    "start_integration_runtime": start_integration_runtime,
    "get_trigger": get_trigger,
    "start_trigger": start_trigger,
    "get_pipeline_definition_raw": get_pipeline_definition_raw,
    "update_pipeline_definition": update_pipeline_definition,
    "list_pipeline_snapshots": list_pipeline_snapshots,
    "rollback_pipeline_definition": rollback_pipeline_definition,
    "back_pipeline_definition": back_pipeline_definition,
    "forward_pipeline_definition": forward_pipeline_definition,
    "stop_trigger": stop_trigger,
    "list_triggers": list_triggers,
    "get_trigger_run_history": get_trigger_run_history,
    "rerun_trigger_run": rerun_trigger_run,
    "cancel_trigger_run": cancel_trigger_run,
    "list_linked_services": list_linked_services,
    "get_linked_service_definition_raw": get_linked_service_definition_raw,
    "update_linked_service_definition": update_linked_service_definition,
    "list_linked_service_snapshots": list_linked_service_snapshots,
    "rollback_linked_service_definition": rollback_linked_service_definition,
    "back_linked_service_definition": back_linked_service_definition,
    "forward_linked_service_definition": forward_linked_service_definition,
    "cancel_pipeline_run": cancel_pipeline_run,
    "list_datasets": list_datasets,
    "get_dataset_definition_raw": get_dataset_definition_raw,
    "update_dataset_definition": update_dataset_definition,
    "list_dataset_snapshots": list_dataset_snapshots,
    "rollback_dataset_definition": rollback_dataset_definition,
    "back_dataset_definition": back_dataset_definition,
    "forward_dataset_definition": forward_dataset_definition,
    "get_data_flow_definition": get_data_flow_definition,
    "update_data_flow_definition": update_data_flow_definition,
    "list_data_flow_snapshots": list_data_flow_snapshots,
    "rollback_data_flow_definition": rollback_data_flow_definition,
    "back_data_flow_definition": back_data_flow_definition,
    "forward_data_flow_definition": forward_data_flow_definition,
    "list_global_parameters": list_global_parameters,
    "get_global_parameter_definition_raw": get_global_parameter_definition_raw,
    "update_global_parameter_definition": update_global_parameter_definition,
    "list_global_parameter_snapshots": list_global_parameter_snapshots,
    "rollback_global_parameter_definition": rollback_global_parameter_definition,
    "back_global_parameter_definition": back_global_parameter_definition,
    "forward_global_parameter_definition": forward_global_parameter_definition,
}
