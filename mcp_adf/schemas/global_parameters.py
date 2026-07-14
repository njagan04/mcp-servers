from mcp.types import Tool, ToolAnnotations

TOOLS = [
    Tool(
        name="create_global_parameter",
        description=(
            "Creates a brand-new global parameter. Fails with an explicit error if one with this name "
            "already exists — use update_global_parameter_definition to modify an existing one instead. "
            "Pushes a \"did not exist\" marker onto this parameter's history stack, so "
            "rollback_global_parameter_definition can undo the creation (delete it) later. `definition` "
            "should be {\"type\": ..., \"value\": ...}, the same flat shape "
            "get_global_parameter_definition_raw uses."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "global_parameter_name": {"type": "string"},
                "definition": {
                    "type": "object",
                    "description": "e.g. {\"type\": \"String\", \"value\": \"...\"}.",
                },
                "reason": {
                    "type": "string",
                    "description": "Why this global parameter is being created — shown in the approval dialog.",
                },
                "state_name": {
                    "type": "string",
                    "description": "Optional name for the created state. Defaults to \"created\" if omitted.",
                },
            },
            "required": ["global_parameter_name", "definition", "reason"],
        },
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True),
    ),
    Tool(
        name="list_global_parameters",
        description=(
            "Factory-wide global parameter sweep — name, type, and value for each. These are the "
            "factory-level parameters referenced by pipelines/datasets/linked services via "
            "@pipeline().globalParameters.<name>."
        ),
        inputSchema={
            "type": "object",
            "properties": {},
            "required": [],
        },
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
    ),
    Tool(
        name="get_global_parameter_definition_raw",
        description=(
            "Full global parameter definition ({\"type\": ..., \"value\": ...}). Feed the returned dict back "
            "into update_global_parameter_definition (with edits applied) to apply a fix."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "global_parameter_name": {"type": "string"},
            },
            "required": ["global_parameter_name"],
        },
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
    ),
    Tool(
        name="update_global_parameter_definition",
        description=(
            "Overwrites a global parameter's type/value (e.g. to fix a stale connection string or a flipped "
            "environment flag baked in as a global). If this parameter has no history yet, its as-found "
            "content is captured as an 'initial' checkpoint first. `definition` must be "
            "get_global_parameter_definition_raw's output with edits applied."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "global_parameter_name": {"type": "string"},
                "definition": {
                    "type": "object",
                    "description": "Modified output of get_global_parameter_definition_raw, e.g. "
                                    "{\"type\": \"String\", \"value\": \"...\"}.",
                },
                "reason": {
                    "type": "string",
                    "description": "The diagnosed root cause driving this change — shown in the approval dialog.",
                },
                "change_summary": {
                    "type": "string",
                    "description": "One-line human-readable diff, e.g. 'point ApiBaseUrl at the new prod endpoint'.",
                },
                "state_name": {
                    "type": "string",
                    "description": "Optional name for the state being saved (what the parameter looked like "
                                    "before this change). Defaults to a slug of change_summary if omitted.",
                },
            },
            "required": ["global_parameter_name", "definition", "reason", "change_summary"],
        },
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True),
    ),
    Tool(
        name="list_global_parameter_snapshots",
        description=(
            "Lists every named state saved in this global parameter's history (state_name, timestamp, reason, "
            "change_summary), oldest first. Query this to see what's available before calling "
            "rollback_global_parameter_definition with a specific state_name."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "global_parameter_name": {"type": "string"},
            },
            "required": ["global_parameter_name"],
        },
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
    ),
    Tool(
        name="rollback_global_parameter_definition",
        description=(
            "Jumps directly to a specific named state from list_global_parameter_snapshots, wherever it sits "
            "in history — nothing is ever deleted. For simple one-step undo/redo without needing a "
            "state_name, use back_global_parameter_definition / forward_global_parameter_definition instead. "
            "If the target state predates the global parameter's creation, this returns requires_confirmation "
            "instead of deleting — ask the human, then re-call with confirm_delete=true."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "global_parameter_name": {"type": "string"},
                "reason": {
                    "type": "string",
                    "description": "Why the change is being reverted — shown in the approval dialog.",
                },
                "state_name": {
                    "type": "string",
                    "description": "Name of the state to jump to, from list_global_parameter_snapshots.",
                },
                "confirm_delete": {
                    "type": "boolean",
                    "description": "Only set true after the human has explicitly agreed to delete the "
                                    "live global parameter, in response to a prior requires_confirmation result.",
                },
            },
            "required": ["global_parameter_name", "reason", "state_name"],
        },
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=True),
    ),
    Tool(
        name="back_global_parameter_definition",
        description=(
            "Steps one checkpoint back through this global parameter's history — see back_pipeline_definition "
            "for the full behavior."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "global_parameter_name": {"type": "string"},
                "reason": {
                    "type": "string",
                    "description": "Why the change is being reverted — shown in the approval dialog.",
                },
                "confirm_delete": {
                    "type": "boolean",
                    "description": "Only set true after the human has explicitly agreed to delete the "
                                    "live global parameter, in response to a prior requires_confirmation result.",
                },
            },
            "required": ["global_parameter_name", "reason"],
        },
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=True),
    ),
    Tool(
        name="forward_global_parameter_definition",
        description=(
            "Steps one checkpoint forward through this global parameter's history — see "
            "forward_pipeline_definition for the full behavior."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "global_parameter_name": {"type": "string"},
                "reason": {
                    "type": "string",
                    "description": "Why the change is being applied — shown in the approval dialog.",
                },
                "confirm_delete": {
                    "type": "boolean",
                    "description": "Only set true after the human has explicitly agreed to delete the "
                                    "live global parameter, in response to a prior requires_confirmation result.",
                },
            },
            "required": ["global_parameter_name", "reason"],
        },
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=True),
    ),
]
