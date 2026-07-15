"""
Thin resource_type -> implementation dispatchers for tool operations that are identical
across resource kinds. Each dispatcher below is a consolidated replacement for what used
to be N separate top-level MCP tools (one per resource kind) — the actual Azure SDK call
logic in datasets.py/pipelines.py/etc. is untouched and reused as-is; only the public
entry point is consolidated.

Collapses only along the resource_type axis (same operation, every kind merged behind one
tool) — never across the mutating/read-only boundary. Each operation here keeps one fixed
annotation in schemas/_generic.py because that operation's mutating-ness is identical
across every kind it covers (verified against the original per-kind schemas before this
consolidation).

Adding a new operation means adding one new *_BY_KIND table + one new function, not
touching the per-kind implementation modules.
"""

from mcp_adf.tools import data_flows, datasets, global_parameters, linked_services, pipelines, triggers

# Every kind's implementation functions take a differently-named "resource name" kwarg
# (pipeline_name, dataset_name, ...) even though the rest of their signature is identical —
# this is the one piece of per-kind knowledge every dispatcher below needs.
_NAME_KWARG_BY_KIND = {
    "pipeline": "pipeline_name",
    "dataset": "dataset_name",
    "linked_service": "service_name",
    "data_flow": "data_flow_name",
    "trigger": "trigger_name",
    "global_parameter": "global_parameter_name",
}


def _by_kind(fn_by_kind: dict) -> dict:
    """{kind: fn} -> {kind: (fn, that kind's name kwarg)}."""
    return {kind: (fn, _NAME_KWARG_BY_KIND[kind]) for kind, fn in fn_by_kind.items()}


def _dispatch_by_name(table: dict, resource_type: str, name: str, **kwargs) -> dict:
    try:
        fn, name_kwarg = table[resource_type]
    except KeyError:
        return {
            "error": "unknown_resource_type",
            "resource_type": resource_type,
            "valid_resource_types": sorted(table),
        }
    return fn(**{name_kwarg: name}, **kwargs)


# get_resource_definition_raw: triggers have no equivalent raw-definition getter
# (get_trigger deliberately returns only name/type/runtime_state, not a full editable
# definition), so "trigger" is intentionally absent here — everywhere else all 6 kinds.
_GET_DEFINITION_RAW_BY_KIND = _by_kind({
    "pipeline": pipelines.get_pipeline_definition_raw,
    "dataset": datasets.get_dataset_definition_raw,
    "linked_service": linked_services.get_linked_service_definition_raw,
    "data_flow": data_flows.get_data_flow_definition,
    "global_parameter": global_parameters.get_global_parameter_definition_raw,
})

_CREATE_BY_KIND = _by_kind({
    "pipeline": pipelines.create_pipeline,
    "dataset": datasets.create_dataset,
    "linked_service": linked_services.create_linked_service,
    "data_flow": data_flows.create_data_flow,
    "trigger": triggers.create_trigger,
    "global_parameter": global_parameters.create_global_parameter,
})

_LIST_BY_KIND = {
    "pipeline": pipelines.list_pipelines,
    "dataset": datasets.list_datasets,
    "linked_service": linked_services.list_linked_services,
    "data_flow": data_flows.list_data_flows,
    "trigger": triggers.list_triggers,
    "global_parameter": global_parameters.list_global_parameters,
}

_UPDATE_DEFINITION_BY_KIND = _by_kind({
    "pipeline": pipelines.update_pipeline_definition,
    "dataset": datasets.update_dataset_definition,
    "linked_service": linked_services.update_linked_service_definition,
    "data_flow": data_flows.update_data_flow_definition,
    "trigger": triggers.update_trigger_definition,
    "global_parameter": global_parameters.update_global_parameter_definition,
})

_LIST_SNAPSHOTS_BY_KIND = _by_kind({
    "pipeline": pipelines.list_pipeline_snapshots,
    "dataset": datasets.list_dataset_snapshots,
    "linked_service": linked_services.list_linked_service_snapshots,
    "data_flow": data_flows.list_data_flow_snapshots,
    "trigger": triggers.list_trigger_snapshots,
    "global_parameter": global_parameters.list_global_parameter_snapshots,
})

_ROLLBACK_BY_KIND = _by_kind({
    "pipeline": pipelines.rollback_pipeline_definition,
    "dataset": datasets.rollback_dataset_definition,
    "linked_service": linked_services.rollback_linked_service_definition,
    "data_flow": data_flows.rollback_data_flow_definition,
    "trigger": triggers.rollback_trigger_definition,
    "global_parameter": global_parameters.rollback_global_parameter_definition,
})

_BACK_BY_KIND = _by_kind({
    "pipeline": pipelines.back_pipeline_definition,
    "dataset": datasets.back_dataset_definition,
    "linked_service": linked_services.back_linked_service_definition,
    "data_flow": data_flows.back_data_flow_definition,
    "trigger": triggers.back_trigger_definition,
    "global_parameter": global_parameters.back_global_parameter_definition,
})

_FORWARD_BY_KIND = _by_kind({
    "pipeline": pipelines.forward_pipeline_definition,
    "dataset": datasets.forward_dataset_definition,
    "linked_service": linked_services.forward_linked_service_definition,
    "data_flow": data_flows.forward_data_flow_definition,
    "trigger": triggers.forward_trigger_definition,
    "global_parameter": global_parameters.forward_global_parameter_definition,
})


def get_resource_definition_raw(resource_type: str, name: str, **kwargs) -> dict:
    """Routes to the matching per-kind get_*_definition_raw implementation."""
    return _dispatch_by_name(_GET_DEFINITION_RAW_BY_KIND, resource_type, name, **kwargs)


def create_resource(resource_type: str, name: str, **kwargs) -> dict:
    """Routes to the matching per-kind create_* implementation."""
    return _dispatch_by_name(_CREATE_BY_KIND, resource_type, name, **kwargs)


def list_resources(resource_type: str, **kwargs) -> dict:
    """Routes to the matching per-kind list_* implementation. No resource name to remap."""
    try:
        fn = _LIST_BY_KIND[resource_type]
    except KeyError:
        return {
            "error": "unknown_resource_type",
            "resource_type": resource_type,
            "valid_resource_types": sorted(_LIST_BY_KIND),
        }
    return fn(**kwargs)


def update_resource_definition(resource_type: str, name: str, **kwargs) -> dict:
    """Routes to the matching per-kind update_*_definition implementation."""
    return _dispatch_by_name(_UPDATE_DEFINITION_BY_KIND, resource_type, name, **kwargs)


def list_resource_snapshots(resource_type: str, name: str, **kwargs) -> dict:
    """Routes to the matching per-kind list_*_snapshots implementation."""
    return _dispatch_by_name(_LIST_SNAPSHOTS_BY_KIND, resource_type, name, **kwargs)


def rollback_resource_definition(resource_type: str, name: str, **kwargs) -> dict:
    """Routes to the matching per-kind rollback_*_definition implementation."""
    return _dispatch_by_name(_ROLLBACK_BY_KIND, resource_type, name, **kwargs)


def back_resource_definition(resource_type: str, name: str, **kwargs) -> dict:
    """Routes to the matching per-kind back_*_definition implementation."""
    return _dispatch_by_name(_BACK_BY_KIND, resource_type, name, **kwargs)


def forward_resource_definition(resource_type: str, name: str, **kwargs) -> dict:
    """Routes to the matching per-kind forward_*_definition implementation."""
    return _dispatch_by_name(_FORWARD_BY_KIND, resource_type, name, **kwargs)
