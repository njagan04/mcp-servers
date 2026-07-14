from azure.core.exceptions import ResourceNotFoundError
from azure.mgmt.datafactory.models import GlobalParameterResource

from mcp_adf.tools._checkpoints import _ensure_baseline, _find_snapshot, _list_snapshots, _navigate, _push_snapshot
from mcp_adf.tools._shared import _client, _reject_if_dropped_fields, _reject_if_miscased, _to_wire_dict

# ADF only allows ONE Global Parameters resource per factory, and its name must literally
# be "default" (confirmed live: any other name raises GlobalParameterNameNotAllowed) — all
# individual parameter names live as keys inside that single resource's `properties` dict,
# not as separate ADF resources. Every tool below therefore reads/writes the whole
# "default" resource and patches a single key in/out of its properties dict; there is no
# such thing as creating or deleting just one parameter's resource in isolation. The local
# checkpoint/rollback history in _checkpoints.py is unaffected by this — it's keyed by
# (factory_name, global_parameter_name) purely for this codebase's own bookkeeping,
# independent of what the real Azure resource is named.
_DEFAULT_RESOURCE_NAME = "default"


def _get_all_properties(client, resource_group: str, factory_name: str) -> dict:
    """Current global parameters as {name: {"type":..., "value":...}}, {} if none exist yet."""
    try:
        resource = client.global_parameters.get(resource_group, factory_name, _DEFAULT_RESOURCE_NAME)
    except ResourceNotFoundError:
        return {}
    wire = _to_wire_dict(resource)
    return wire if isinstance(wire, dict) else {}


def _write_all_properties(client, resource_group: str, factory_name: str, properties: dict):
    """
    Writes the full properties dict back as the singleton "default" resource. Returns
    (error_dict, None) on validation failure, else (None, updated_resource).
    """
    error = _reject_if_dropped_fields({"properties": properties}, GlobalParameterResource, "global parameter")
    if error:
        return error, None
    resource = GlobalParameterResource.deserialize({"properties": properties})
    error = _reject_if_miscased(resource, "global parameter")
    if error:
        return error, None
    updated = client.global_parameters.create_or_update(
        resource_group, factory_name, _DEFAULT_RESOURCE_NAME, resource
    )
    return None, updated


def list_global_parameters(
    factory_name: str,
    subscription_id: str, resource_group: str,
    tenant_id: str, client_id: str, client_secret: str,
) -> dict:
    """
    Factory-wide global parameter sweep — name, type, and value for each. These are the
    factory-level parameters referenced by pipelines/datasets/linked services via
    @pipeline().globalParameters.<name>. Returns an empty list if the factory has no
    global parameters defined at all (there is no separate resource to create until the
    first one is added — see create_global_parameter).
    """
    client = _client(tenant_id, client_id, client_secret, subscription_id)
    properties = _get_all_properties(client, resource_group, factory_name)
    return {
        "global_parameters": [
            {"name": name, "type": spec.get("type"), "value": spec.get("value")}
            for name, spec in properties.items()
        ]
    }


def get_global_parameter_definition_raw(
    global_parameter_name: str, factory_name: str,
    subscription_id: str, resource_group: str,
    tenant_id: str, client_id: str, client_secret: str,
) -> dict:
    """
    Full global parameter definition ({"type": ..., "value": ...}). Feed the returned dict
    back into update_global_parameter_definition (with edits applied) to apply a fix — e.g.
    a stale connection string or environment flag baked in as a global.
    """
    client = _client(tenant_id, client_id, client_secret, subscription_id)
    properties = _get_all_properties(client, resource_group, factory_name)
    if global_parameter_name not in properties:
        return {"error": "global_parameter_not_found", "global_parameter_name": global_parameter_name}
    return properties[global_parameter_name]


def create_global_parameter(
    global_parameter_name: str, factory_name: str,
    subscription_id: str, resource_group: str,
    tenant_id: str, client_id: str, client_secret: str,
    definition: dict,
    reason: str,
    state_name: str | None = None,
) -> dict:
    """
    Adds a brand-new global parameter. Fails with an explicit error if one with this name
    already exists — use update_global_parameter_definition to modify an existing one
    instead. Records two checkpoints in this parameter's history: "before-creation" (it
    didn't exist — rollback here removes it) and one for the just-created content, named
    `state_name` if given (default "created") — so list_global_parameter_snapshots and
    rollback_global_parameter_definition work on it from the start, same as any updated
    global parameter. `definition` should be {"type": ..., "value": ...}. Under the hood
    this reads the factory's whole set of global parameters, adds this one, and writes
    the full set back — ADF has no notion of creating a single parameter in isolation.
    """
    client = _client(tenant_id, client_id, client_secret, subscription_id)
    properties = _get_all_properties(client, resource_group, factory_name)

    if global_parameter_name in properties:
        return {"error": "global_parameter_already_exists", "global_parameter_name": global_parameter_name}

    _push_snapshot(
        "globalparameter", factory_name, global_parameter_name,
        definition=None, reason=reason, change_summary="global parameter did not exist",
        state_name="before-creation", action="create",
    )

    merged = {**properties, global_parameter_name: definition}
    error, updated = _write_all_properties(client, resource_group, factory_name, merged)
    if error:
        return error
    updated_properties = _to_wire_dict(updated)

    saved = _push_snapshot(
        "globalparameter", factory_name, global_parameter_name,
        definition=updated_properties.get(global_parameter_name),
        reason=reason, change_summary="global parameter created", state_name=state_name or "created",
    )
    return {
        "global_parameter_name": global_parameter_name,
        "created": True,
        "reason": reason,
        "saved_state_name": saved["state_name"],
        "etag": updated.etag,
    }


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
    Overwrites a global parameter's type/value. Fails with an explicit error if no
    parameter with this name exists yet — use create_global_parameter first. If this
    parameter has no local history yet, its as-found content is captured as an "initial"
    checkpoint first. After applying the change, the NEW content is pushed as a named
    checkpoint — see update_pipeline_definition for the full history design. `definition`
    should be get_global_parameter_definition_raw's output ({"type": ..., "value": ...})
    with edits applied.
    """
    client = _client(tenant_id, client_id, client_secret, subscription_id)
    properties = _get_all_properties(client, resource_group, factory_name)

    if global_parameter_name not in properties:
        return {"error": "global_parameter_not_found", "global_parameter_name": global_parameter_name}

    _ensure_baseline("globalparameter", factory_name, global_parameter_name, properties[global_parameter_name], reason)

    merged = {**properties, global_parameter_name: definition}
    error, updated = _write_all_properties(client, resource_group, factory_name, merged)
    if error:
        return error
    updated_properties = _to_wire_dict(updated)

    saved = _push_snapshot(
        "globalparameter", factory_name, global_parameter_name,
        definition=updated_properties.get(global_parameter_name),
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
    of where it sits in history — nothing is ever deleted from local history. For simple
    one-step undo/redo without needing a state_name, use back_global_parameter_definition /
    forward_global_parameter_definition instead.

    If the target checkpoint predates the global parameter's creation, applying it means
    REMOVING it from the factory's global parameters — this returns
    {"requires_confirmation": true} instead of removing it; call again with
    confirm_delete=true only after the human has agreed to that.
    """
    client = _client(tenant_id, client_id, client_secret, subscription_id)

    target = _find_snapshot("globalparameter", factory_name, global_parameter_name, state_name)
    if target is None:
        return {"error": "state_not_found", "global_parameter_name": global_parameter_name, "state_name": state_name}

    properties = _get_all_properties(client, resource_group, factory_name)

    if target["action"] == "create":
        if not confirm_delete:
            return {
                "requires_confirmation": True,
                "would": "delete",
                "global_parameter_name": global_parameter_name,
                "target_state": target["state_name"],
                "message": (
                    f"'{target['state_name']}' predates this global parameter's creation — rolling "
                    "back to it means REMOVING it from the factory's global parameters. Ask the "
                    "human before proceeding; call again with confirm_delete=true only after they agree."
                ),
            }
        remaining = {k: v for k, v in properties.items() if k != global_parameter_name}
        if remaining:
            error, _ = _write_all_properties(client, resource_group, factory_name, remaining)
            if error:
                return error
        else:
            client.global_parameters.delete(resource_group, factory_name, _DEFAULT_RESOURCE_NAME)
        _push_snapshot(
            "globalparameter", factory_name, global_parameter_name,
            definition=None, reason=reason, change_summary=f"rolled back to '{target['state_name']}'",
            state_name=target["state_name"], action="create",
        )
        return {
            "global_parameter_name": global_parameter_name, "rolled_back_to": target["state_name"],
            "deleted": True, "reason": reason,
        }

    merged = {**properties, global_parameter_name: target["definition"]}
    error, updated = _write_all_properties(client, resource_group, factory_name, merged)
    if error:
        return error
    updated_properties = _to_wire_dict(updated)
    _push_snapshot(
        "globalparameter", factory_name, global_parameter_name,
        definition=updated_properties.get(global_parameter_name), reason=reason,
        change_summary=f"rolled back to '{target['state_name']}'", state_name=target["state_name"],
    )
    return {"global_parameter_name": global_parameter_name, "rolled_back_to": target["state_name"], "reason": reason}


def _global_parameter_navigate(
    global_parameter_name: str, factory_name: str, subscription_id: str, resource_group: str,
    tenant_id: str, client_id: str, client_secret: str, reason: str, direction: str, confirm_delete: bool,
) -> dict:
    client = _client(tenant_id, client_id, client_secret, subscription_id)

    def delete() -> None:
        properties = _get_all_properties(client, resource_group, factory_name)
        remaining = {k: v for k, v in properties.items() if k != global_parameter_name}
        if remaining:
            _write_all_properties(client, resource_group, factory_name, remaining)
        else:
            client.global_parameters.delete(resource_group, factory_name, _DEFAULT_RESOURCE_NAME)

    def apply(definition: dict) -> dict | None:
        properties = _get_all_properties(client, resource_group, factory_name)
        merged = {**properties, global_parameter_name: definition}
        error, _ = _write_all_properties(client, resource_group, factory_name, merged)
        return error

    return _navigate(
        "globalparameter", factory_name, global_parameter_name, "global_parameter_name",
        direction, reason, confirm_delete,
        delete_fn=delete,
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
