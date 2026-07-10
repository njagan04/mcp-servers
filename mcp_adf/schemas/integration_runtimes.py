from mcp.types import Tool, ToolAnnotations

TOOLS = [
    Tool(
        name="get_integration_runtime_status",
        description=(
            "Get an integration runtime's state (works for Azure, self-hosted, and Azure-SSIS IR types). "
            "Use this before start_integration_runtime to check whether starting it is even applicable."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "integration_runtime_name": {"type": "string"},
            },
            "required": ["integration_runtime_name"],
        },
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
    ),
    Tool(
        name="start_integration_runtime",
        description=(
            "Starts a stopped Azure-SSIS (managed) integration runtime. Does NOT work on self-hosted IRs "
            "(no remote-start API exists for those — restarting a self-hosted IR's on-prem service is human-only). "
            "Only call this after get_integration_runtime_status confirms the IR is a managed type and is Stopped."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "integration_runtime_name": {"type": "string"},
                "reason": {
                    "type": "string",
                    "description": "Why this fix was chosen — shown to the user in the approval dialog.",
                },
            },
            "required": ["integration_runtime_name", "reason"],
        },
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True),
    ),
]
