from mcp.types import Tool, ToolAnnotations

TOOLS = [
    Tool(
        name="get_linked_service",
        description=(
            "Name and type only (e.g. \"AzureSqlDatabase\") — does NOT include the actual "
            "configured host/port/connection string. Use get_resource_definition_raw "
            "(resource_type=\"linked_service\") for the real connection details."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "service_name": {"type": "string"},
            },
            "required": ["service_name"],
        },
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
    ),
]
