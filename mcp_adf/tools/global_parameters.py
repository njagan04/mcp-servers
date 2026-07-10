from azure.mgmt.datafactory.models import GlobalParameterResource

from mcp_adf.tools._checkpoints import _ensure_baseline, _find_snapshot, _list_snapshots, _navigate, _push_snapshot
from mcp_adf.tools._shared import _client, _reject_if_miscased, _to_wire_dict


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
