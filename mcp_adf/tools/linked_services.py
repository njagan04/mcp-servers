from azure.mgmt.datafactory.models import LinkedServiceResource

from mcp_adf.tools._checkpoints import _ensure_baseline, _find_snapshot, _list_snapshots, _navigate, _push_snapshot
from mcp_adf.tools._shared import _client, _reject_if_miscased, _to_wire_dict


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


