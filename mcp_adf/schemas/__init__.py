from mcp_adf.schemas import (
    data_flows,
    datasets,
    global_parameters,
    integration_runtimes,
    linked_services,
    pipelines,
    triggers,
)

# Tool schemas, assembled from each resource-type module in the same categorization
# as mcp_adf.tools. Imported by server.py to build its list_tools() response.
TOOLS = (
    pipelines.TOOLS
    + linked_services.TOOLS
    + integration_runtimes.TOOLS
    + triggers.TOOLS
    + datasets.TOOLS
    + data_flows.TOOLS
    + global_parameters.TOOLS
)
