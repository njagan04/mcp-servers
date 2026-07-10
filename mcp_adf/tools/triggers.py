from datetime import datetime, timedelta, timezone

from azure.mgmt.datafactory.models import RunFilterParameters

from mcp_adf.tools._shared import _client


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


