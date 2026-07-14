from azure.core.exceptions import ResourceNotFoundError
from azure.mgmt.datafactory.models import DataFlowResource

from mcp_adf.tools._checkpoints import _ensure_baseline, _find_snapshot, _list_snapshots, _navigate, _push_snapshot
from mcp_adf.tools._shared import _client, _reject_if_dropped_fields, _reject_if_miscased, _to_wire_dict


def list_data_flows(
    factory_name: str,
    subscription_id: str, resource_group: str,
    tenant_id: str, client_id: str, client_secret: str,
) -> dict:
    """Factory-wide data flow sweep — name and type (e.g. MappingDataFlow) for each."""
    client = _client(tenant_id, client_id, client_secret, subscription_id)
    data_flows = client.data_flows.list_by_factory(resource_group, factory_name)
    return {
        "data_flows": [
            {
                "name": df.name,
                "type": df.properties.type if df.properties else "unknown",
            }
            for df in data_flows
        ]
    }


def create_data_flow(
    data_flow_name: str, factory_name: str,
    subscription_id: str, resource_group: str,
    tenant_id: str, client_id: str, client_secret: str,
    definition: dict,
    reason: str,
    state_name: str | None = None,
) -> dict:
    """
    Creates a brand-new data flow. Fails with an explicit error if a data flow with this
    name already exists — use update_data_flow_definition to modify an existing one
    instead. Records two checkpoints in this data flow's history: "before-creation" (it
    didn't exist — rollback here deletes it) and one for the just-created content, named
    `state_name` if given (default "created") — so list_data_flow_snapshots and
    rollback_data_flow_definition work on it from the start, same as any updated data flow.
    `definition` accepts either shape:
      - the flat shape get_data_flow_definition/update_data_flow_definition use
        (type/sources/sinks/transformations/script/... at the top level), or
      - the ARM / Data Factory Studio export shape
        ({"name": ..., "properties": {"type": "MappingDataFlow", ...}}) — if a "properties"
        key is present, its contents are used and the wrapper (including its own "name")
        is discarded. `data_flow_name` is always what determines the actual name created.
    """
    client = _client(tenant_id, client_id, client_secret, subscription_id)

    try:
        client.data_flows.get(resource_group, factory_name, data_flow_name)
        return {"error": "data_flow_already_exists", "data_flow_name": data_flow_name}
    except ResourceNotFoundError:
        pass

    _push_snapshot(
        "dataflow", factory_name, data_flow_name,
        definition=None, reason=reason, change_summary="data flow did not exist",
        state_name="before-creation", action="create",
    )

    properties = definition.get("properties", definition)
    error = _reject_if_dropped_fields({"properties": properties}, DataFlowResource, "data flow")
    if error:
        return error
    data_flow_resource = DataFlowResource.deserialize({"properties": properties})
    error = _reject_if_miscased(data_flow_resource, "data flow")
    if error:
        return error
    created = client.data_flows.create_or_update(resource_group, factory_name, data_flow_name, data_flow_resource)

    saved = _push_snapshot(
        "dataflow", factory_name, data_flow_name,
        definition=_to_wire_dict(created), reason=reason, change_summary="data flow created",
        state_name=state_name or "created",
    )
    return {
        "data_flow_name": data_flow_name,
        "created": True,
        "reason": reason,
        "saved_state_name": saved["state_name"],
        "etag": created.etag,
    }


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

    error = _reject_if_dropped_fields({"properties": definition}, DataFlowResource, "data flow")
    if error:
        return error
    data_flow_resource = DataFlowResource.deserialize({"properties": definition})
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

    error = _reject_if_dropped_fields({"properties": target["definition"]}, DataFlowResource, "data flow")
    if error:
        return error
    data_flow_resource = DataFlowResource.deserialize({"properties": target["definition"]})
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
        error = _reject_if_dropped_fields({"properties": definition}, DataFlowResource, "data flow")
        if error:
            return error
        data_flow_resource = DataFlowResource.deserialize({"properties": definition})
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


