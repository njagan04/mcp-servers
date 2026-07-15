from collections.abc import Callable

from mcp_adf.tools import (
    _dispatch,
    data_flows,
    datasets,
    global_parameters,
    integration_runtimes,
    linked_services,
    pipelines,
    triggers,
)

# Single source of truth for the ADF tool registry, assembled from each resource-type
# module. Imported by server.py (stdio MCP path).
#
# create_*/list_*/update_*_definition/list_*_snapshots/rollback_*_definition/
# back_*_definition/forward_*_definition and get_*_definition_raw used to be separate
# per-resource-kind tools here (one entry per kind x operation). They're consolidated
# behind mcp_adf.tools._dispatch's resource_type-parameterized dispatchers instead — the
# per-kind implementation functions below are unchanged and still do the real work, they're
# just no longer each exposed as their own top-level MCP tool. Only genuinely kind-specific
# operations (pipeline run/activity diagnostics, trigger start/stop/rerun, get_linked_service,
# integration runtime status/start) stay as dedicated entries.
TOOL_REGISTRY: dict[str, Callable[..., dict]] = {
    "get_pipeline_definition": pipelines.get_pipeline_definition,
    "get_pipeline_run_status": pipelines.get_pipeline_run_status,
    "get_pipeline_run_history": pipelines.get_pipeline_run_history,
    "list_pipeline_runs": pipelines.list_pipeline_runs,
    "get_activity_run_history": pipelines.get_activity_run_history,
    "get_activity_run_error": pipelines.get_activity_run_error,
    "list_activity_runs": pipelines.list_activity_runs,
    "get_activity_run_io": pipelines.get_activity_run_io,
    "rerun_pipeline": pipelines.rerun_pipeline,
    "cancel_pipeline_run": pipelines.cancel_pipeline_run,

    "get_trigger": triggers.get_trigger,
    "get_trigger_run_history": triggers.get_trigger_run_history,
    "start_trigger": triggers.start_trigger,
    "stop_trigger": triggers.stop_trigger,
    "rerun_trigger_run": triggers.rerun_trigger_run,
    "cancel_trigger_run": triggers.cancel_trigger_run,

    "get_linked_service": linked_services.get_linked_service,

    "get_integration_runtime_status": integration_runtimes.get_integration_runtime_status,
    "start_integration_runtime": integration_runtimes.start_integration_runtime,

    "get_resource_definition_raw": _dispatch.get_resource_definition_raw,
    "create_resource": _dispatch.create_resource,
    "list_resources": _dispatch.list_resources,
    "update_resource_definition": _dispatch.update_resource_definition,
    "list_resource_snapshots": _dispatch.list_resource_snapshots,
    "rollback_resource_definition": _dispatch.rollback_resource_definition,
    "back_resource_definition": _dispatch.back_resource_definition,
    "forward_resource_definition": _dispatch.forward_resource_definition,
}
