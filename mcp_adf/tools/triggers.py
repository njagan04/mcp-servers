from datetime import datetime, timedelta, timezone

from azure.core.exceptions import ResourceNotFoundError
from azure.mgmt.datafactory.models import RunFilterParameters, TriggerResource

from mcp_adf.tools._checkpoints import _ensure_baseline, _find_snapshot, _list_snapshots, _navigate, _push_snapshot
from mcp_adf.tools._shared import _client, _reject_if_miscased, _to_ist, _to_wire_dict


def create_trigger(
    trigger_name: str, factory_name: str,
    subscription_id: str, resource_group: str,
    tenant_id: str, client_id: str, client_secret: str,
    definition: dict,
    reason: str,
    state_name: str | None = None,
) -> dict:
    """
    Creates a brand-new trigger (e.g. a ScheduleTrigger). Fails with an explicit error if
    a trigger with this name already exists — use update_trigger_definition to modify an
    existing one instead. Created in a Stopped state, same as ADF Studio's default — call
    start_trigger separately once you've verified it. Records two checkpoints in this
    trigger's history: "before-creation" (it didn't exist — rollback here deletes it) and
    one for the just-created content, named `state_name` if given (default "created") — so
    list_trigger_snapshots and rollback_trigger_definition work on it from the start.
    `definition` should be the flat shape (properties.type, properties.typeProperties,
    pipelines, etc. at the top level) matching what get_trigger's raw definition would show.
    """
    client = _client(tenant_id, client_id, client_secret, subscription_id)

    try:
        client.triggers.get(resource_group, factory_name, trigger_name)
        return {"error": "trigger_already_exists", "trigger_name": trigger_name}
    except ResourceNotFoundError:
        pass

    _push_snapshot(
        "trigger", factory_name, trigger_name,
        definition=None, reason=reason, change_summary="trigger did not exist",
        state_name="before-creation", action="create",
    )

    trigger_resource = TriggerResource.deserialize({"properties": definition})
    error = _reject_if_miscased(trigger_resource, "trigger")
    if error:
        return error
    created = client.triggers.create_or_update(resource_group, factory_name, trigger_name, trigger_resource)

    saved = _push_snapshot(
        "trigger", factory_name, trigger_name,
        definition=_to_wire_dict(created), reason=reason, change_summary="trigger created",
        state_name=state_name or "created",
    )
    return {
        "trigger_name": trigger_name,
        "created": True,
        "reason": reason,
        "saved_state_name": saved["state_name"],
        "etag": created.etag,
    }


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
                "timestamp_ist": _to_ist(r.trigger_run_timestamp),
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


def update_trigger_definition(
    trigger_name: str, factory_name: str,
    subscription_id: str, resource_group: str,
    tenant_id: str, client_id: str, client_secret: str,
    definition: dict,
    reason: str,
    change_summary: str,
    state_name: str | None = None,
) -> dict:
    """
    Overwrites a trigger's full definition (e.g. to correct a wrong schedule/recurrence).
    If this trigger has no history yet, its as-found content is captured as an "initial"
    checkpoint first. After applying the change, the NEW content is pushed as a named
    checkpoint — see update_pipeline_definition for the full history design. Does not
    change the trigger's Started/Stopped runtime state — use start_trigger/stop_trigger
    for that. `definition` should be the flat shape matching get_trigger's raw definition.
    """
    client = _client(tenant_id, client_id, client_secret, subscription_id)
    current = client.triggers.get(resource_group, factory_name, trigger_name)
    _ensure_baseline("trigger", factory_name, trigger_name, _to_wire_dict(current), reason)

    trigger_resource = TriggerResource.deserialize({"properties": definition})
    error = _reject_if_miscased(trigger_resource, "trigger")
    if error:
        return error
    updated = client.triggers.create_or_update(resource_group, factory_name, trigger_name, trigger_resource)

    saved = _push_snapshot(
        "trigger", factory_name, trigger_name,
        definition=_to_wire_dict(updated), reason=reason, change_summary=change_summary,
        state_name=state_name,
    )
    return {
        "trigger_name": trigger_name,
        "updated": True,
        "reason": reason,
        "change_summary": change_summary,
        "saved_state_name": saved["state_name"],
        "etag": updated.etag,
    }


def list_trigger_snapshots(
    trigger_name: str, factory_name: str,
    subscription_id: str, resource_group: str,
    tenant_id: str, client_id: str, client_secret: str,
) -> dict:
    """Lists every named checkpoint saved for this trigger, oldest first."""
    return {"trigger_name": trigger_name, "states": _list_snapshots("trigger", factory_name, trigger_name)}


def rollback_trigger_definition(
    trigger_name: str, factory_name: str,
    subscription_id: str, resource_group: str,
    tenant_id: str, client_id: str, client_secret: str,
    reason: str,
    state_name: str,
    confirm_delete: bool = False,
) -> dict:
    """
    Jumps directly to any named checkpoint from list_trigger_snapshots, regardless of
    where it sits in history — nothing is ever deleted. For simple one-step undo/redo
    without needing a state_name, use back_trigger_definition / forward_trigger_definition
    instead.

    If the target checkpoint predates the trigger's creation, applying it means DELETING
    it — this returns {"requires_confirmation": true} instead of deleting; call again with
    confirm_delete=true only after the human has agreed to that.
    """
    client = _client(tenant_id, client_id, client_secret, subscription_id)

    target = _find_snapshot("trigger", factory_name, trigger_name, state_name)
    if target is None:
        return {"error": "state_not_found", "trigger_name": trigger_name, "state_name": state_name}

    if target["action"] == "create":
        if not confirm_delete:
            return {
                "requires_confirmation": True,
                "would": "delete",
                "trigger_name": trigger_name,
                "target_state": target["state_name"],
                "message": (
                    f"'{target['state_name']}' predates this trigger's creation — rolling "
                    "back to it means DELETING it in Azure. Ask the human before proceeding; call "
                    "again with confirm_delete=true only after they agree."
                ),
            }
        client.triggers.delete(resource_group, factory_name, trigger_name)
        _push_snapshot(
            "trigger", factory_name, trigger_name,
            definition=None, reason=reason, change_summary=f"rolled back to '{target['state_name']}'",
            state_name=target["state_name"], action="create",
        )
        return {"trigger_name": trigger_name, "rolled_back_to": target["state_name"], "deleted": True, "reason": reason}

    trigger_resource = TriggerResource.deserialize({"properties": target["definition"]})
    error = _reject_if_miscased(trigger_resource, "trigger")
    if error:
        return error
    updated = client.triggers.create_or_update(resource_group, factory_name, trigger_name, trigger_resource)
    _push_snapshot(
        "trigger", factory_name, trigger_name,
        definition=_to_wire_dict(updated), reason=reason, change_summary=f"rolled back to '{target['state_name']}'",
        state_name=target["state_name"],
    )
    return {"trigger_name": trigger_name, "rolled_back_to": target["state_name"], "reason": reason}


def _trigger_navigate(
    trigger_name: str, factory_name: str, subscription_id: str, resource_group: str,
    tenant_id: str, client_id: str, client_secret: str, reason: str, direction: str, confirm_delete: bool,
) -> dict:
    client = _client(tenant_id, client_id, client_secret, subscription_id)

    def apply(definition: dict) -> dict | None:
        trigger_resource = TriggerResource.deserialize({"properties": definition})
        error = _reject_if_miscased(trigger_resource, "trigger")
        if error:
            return error
        client.triggers.create_or_update(resource_group, factory_name, trigger_name, trigger_resource)
        return None

    return _navigate(
        "trigger", factory_name, trigger_name, "trigger_name", direction, reason, confirm_delete,
        delete_fn=lambda: client.triggers.delete(resource_group, factory_name, trigger_name),
        apply_fn=apply,
    )


def back_trigger_definition(
    trigger_name: str, factory_name: str,
    subscription_id: str, resource_group: str,
    tenant_id: str, client_id: str, client_secret: str,
    reason: str,
    confirm_delete: bool = False,
) -> dict:
    """Steps one checkpoint back through this trigger's history — see back_pipeline_definition."""
    return _trigger_navigate(
        trigger_name, factory_name, subscription_id, resource_group,
        tenant_id, client_id, client_secret, reason, "back", confirm_delete,
    )


def forward_trigger_definition(
    trigger_name: str, factory_name: str,
    subscription_id: str, resource_group: str,
    tenant_id: str, client_id: str, client_secret: str,
    reason: str,
    confirm_delete: bool = False,
) -> dict:
    """Steps one checkpoint forward through this trigger's history — see forward_pipeline_definition."""
    return _trigger_navigate(
        trigger_name, factory_name, subscription_id, resource_group,
        tenant_id, client_id, client_secret, reason, "forward", confirm_delete,
    )


