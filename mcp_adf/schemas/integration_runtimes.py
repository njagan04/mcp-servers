from mcp.types import Tool, ToolAnnotations

TOOLS = [
    Tool(
        name="get_integration_runtime_status",
        description="Get an integration runtime's state (Azure, self-hosted, or Azure-SSIS IR types).",
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
            "Starts a stopped Azure-SSIS (managed) integration runtime. Does NOT work on "
            "self-hosted IRs — no remote-start API exists; that's human-only."
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
