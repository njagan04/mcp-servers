from azure.core.exceptions import ResourceNotFoundError
from azure.mgmt.datafactory.models import DatasetResource

from mcp_adf.tools._checkpoints import _ensure_baseline, _find_snapshot, _list_snapshots, _navigate, _push_snapshot
from mcp_adf.tools._shared import _client, _reject_if_dropped_fields, _reject_if_miscased, _to_wire_dict


def create_dataset(
    dataset_name: str, factory_name: str,
    subscription_id: str, resource_group: str,
    tenant_id: str, client_id: str, client_secret: str,
    definition: dict,
    reason: str,
    state_name: str | None = None,
) -> dict:
    """
    Creates a brand-new dataset. Fails with an explicit error if a dataset with this name
    already exists — use update_dataset_definition to modify an existing one instead.
    Records two checkpoints in this dataset's history: "before-creation" (it didn't exist —
    rollback here deletes it) and one for the just-created content, named `state_name` if
    given (default "created") — so list_dataset_snapshots and rollback_dataset_definition
    work on it from the start, same as any updated dataset. `definition` accepts either
    shape:
      - the flat shape get_dataset_definition_raw/update_dataset_definition use
        (type/schema/typeProperties/linkedServiceName/... at the top level), or
      - the ARM / Data Factory Studio export shape
        ({"name": ..., "properties": {"type": "...", ...}}) — if a "properties" key is
        present, its contents are used and the wrapper (including its own "name")
        is discarded. `dataset_name` is always what determines the actual name created.
    """
    client = _client(tenant_id, client_id, client_secret, subscription_id)

    try:
        client.datasets.get(resource_group, factory_name, dataset_name)
        return {"error": "dataset_already_exists", "dataset_name": dataset_name}
    except ResourceNotFoundError:
        pass

    _push_snapshot(
        "dataset", factory_name, dataset_name,
        definition=None, reason=reason, change_summary="dataset did not exist",
        state_name="before-creation", action="create",
    )

    properties = definition.get("properties", definition)
    error = _reject_if_dropped_fields({"properties": properties}, DatasetResource, "dataset")
    if error:
        return error
    dataset_resource = DatasetResource.deserialize({"properties": properties})
    error = _reject_if_miscased(dataset_resource, "dataset")
    if error:
        return error
    created = client.datasets.create_or_update(resource_group, factory_name, dataset_name, dataset_resource)

    saved = _push_snapshot(
        "dataset", factory_name, dataset_name,
        definition=_to_wire_dict(created), reason=reason, change_summary="dataset created",
        state_name=state_name or "created",
    )
    return {
        "dataset_name": dataset_name,
        "created": True,
        "reason": reason,
        "saved_state_name": saved["state_name"],
        "etag": created.etag,
    }


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

    error = _reject_if_dropped_fields({"properties": definition}, DatasetResource, "dataset")
    if error:
        return error
    dataset_resource = DatasetResource.deserialize({"properties": definition})
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

    error = _reject_if_dropped_fields({"properties": target["definition"]}, DatasetResource, "dataset")
    if error:
        return error
    dataset_resource = DatasetResource.deserialize({"properties": target["definition"]})
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
        error = _reject_if_dropped_fields({"properties": definition}, DatasetResource, "dataset")
        if error:
            return error
        dataset_resource = DatasetResource.deserialize({"properties": definition})
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


