from mcp_adf.schemas import (
    _generic,
    integration_runtimes,
    linked_services,
    pipelines,
    triggers,
)

# Tool schemas, assembled from each resource-type module (only kind-specific tools left —
# datasets/data_flows/global_parameters had nothing kind-specific remaining after the
# create/list/update/snapshot/rollback/back/forward/get_*_definition_raw family was
# consolidated into _generic's resource_type-parameterized tools) plus _generic itself.
# Imported by server.py to build its list_tools() response.
TOOLS = (
    pipelines.TOOLS
    + linked_services.TOOLS
    + integration_runtimes.TOOLS
    + triggers.TOOLS
    + _generic.TOOLS
)
