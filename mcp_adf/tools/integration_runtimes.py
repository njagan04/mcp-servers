from mcp_adf.tools._shared import _client


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


