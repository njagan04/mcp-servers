from mcp.types import Tool, ToolAnnotations

TOOLS = [
    Tool(
        name="get_linked_service",
        description=(
            "Name and type only (e.g. \"AzureSqlDatabase\") — does NOT include the actual "
            "configured host/port/connection string. Use get_linked_service_definition_raw "
            "for the real connection details."
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
    Tool(
        name="list_linked_services",
        description=(
            "Factory-wide linked-service sweep, vs. get_linked_service's single lookup. Useful when "
            "the failing linked service isn't known by name up front."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "factory_name": {"type": "string"},
            },
            "required": ["factory_name"],
        },
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
    ),
    Tool(
        name="get_linked_service_definition_raw",
        description=(
            "Full linked-service definition — the actual configured host/port/connection "
            "string live in typeProperties (e.g. AzureSqlDatabase's "
            "typeProperties.connectionString), which get_linked_service omits. Use during "
            "diagnosis of network/config failures to see the real configured server "
            "address, and as the editable structure for update_linked_service_definition."
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
    Tool(
        name="update_linked_service_definition",
        description=(
            "Overwrites a linked service's full definition (e.g. to correct a wrong "
            "host/port). `definition` must be get_linked_service_definition_raw's output "
            "with edits applied. The pre-change definition is pushed onto this linked "
            "service's named history stack automatically, so "
            "rollback_linked_service_definition can jump back to it later by name (see "
            "list_linked_service_snapshots)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "service_name": {"type": "string"},
                "definition": {"type": "object", "description": "Modified output of get_linked_service_definition_raw."},
                "reason": {
                    "type": "string",
                    "description": "The diagnosed root cause driving this change — shown in the approval dialog.",
                },
                "change_summary": {
                    "type": "string",
                    "description": "One-line human-readable diff of what changed.",
                },
                "state_name": {
                    "type": "string",
                    "description": "Optional name for the state being saved. Defaults to a slug of "
                                    "change_summary if omitted.",
                },
            },
            "required": ["service_name", "definition", "reason", "change_summary"],
        },
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True),
    ),
    Tool(
        name="list_linked_service_snapshots",
        description="Lists every named state saved in this linked service's history, oldest first.",
        inputSchema={
            "type": "object",
            "properties": {
                "service_name": {"type": "string"},
            },
            "required": ["service_name"],
        },
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
    ),
    Tool(
        name="rollback_linked_service_definition",
        description=(
            "Jumps directly to a specific named state from list_linked_service_snapshots, wherever it sits "
            "in history — nothing is ever deleted. For simple one-step undo/redo without needing a "
            "state_name, use back_linked_service_definition / forward_linked_service_definition instead. If "
            "the target state predates the linked service's creation, this returns requires_confirmation "
            "instead of deleting — ask the human, then re-call with confirm_delete=true."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "service_name": {"type": "string"},
                "reason": {
                    "type": "string",
                    "description": "Why the change is being reverted — shown in the approval dialog.",
                },
                "state_name": {
                    "type": "string",
                    "description": "Name of the state to jump to, from list_linked_service_snapshots.",
                },
                "confirm_delete": {
                    "type": "boolean",
                    "description": "Only set true after the human has explicitly agreed to delete the "
                                    "live linked service, in response to a prior requires_confirmation result.",
                },
            },
            "required": ["service_name", "reason", "state_name"],
        },
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=True),
    ),
    Tool(
        name="back_linked_service_definition",
        description=(
            "Steps one checkpoint back through this linked service's history — see "
            "back_pipeline_definition for the full behavior."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "service_name": {"type": "string"},
                "reason": {
                    "type": "string",
                    "description": "Why the change is being reverted — shown in the approval dialog.",
                },
                "confirm_delete": {
                    "type": "boolean",
                    "description": "Only set true after the human has explicitly agreed to delete the "
                                    "live linked service, in response to a prior requires_confirmation result.",
                },
            },
            "required": ["service_name", "reason"],
        },
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=True),
    ),
    Tool(
        name="forward_linked_service_definition",
        description=(
            "Steps one checkpoint forward through this linked service's history — see "
            "forward_pipeline_definition for the full behavior."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "service_name": {"type": "string"},
                "reason": {
                    "type": "string",
                    "description": "Why the change is being applied — shown in the approval dialog.",
                },
                "confirm_delete": {
                    "type": "boolean",
                    "description": "Only set true after the human has explicitly agreed to delete the "
                                    "live linked service, in response to a prior requires_confirmation result.",
                },
            },
            "required": ["service_name", "reason"],
        },
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=True),
    ),
]
