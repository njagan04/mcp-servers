from mcp.types import Tool, ToolAnnotations

TOOLS = [
    Tool(
        name="get_data_flow_definition",
        description=(
            "Full Mapping Data Flow definition (sources, sinks, transformation script). Pipeline "
            "definition tools only show that an activity references a data flow by name — this is the "
            "only way to see (and diagnose schema_drift/business_logic failures inside) the "
            "transformation graph itself."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "data_flow_name": {"type": "string"},
            },
            "required": ["data_flow_name"],
        },
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
    ),
    Tool(
        name="update_data_flow_definition",
        description=(
            "Overwrites a data flow's full definition to apply a concrete fix. `definition` must be "
            "get_data_flow_definition's output with edits applied. The pre-change definition is pushed "
            "onto this data flow's named history stack automatically, so rollback_data_flow_definition "
            "can jump back to it later by name (see list_data_flow_snapshots)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "data_flow_name": {"type": "string"},
                "definition": {"type": "object", "description": "Modified output of get_data_flow_definition."},
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
            "required": ["data_flow_name", "definition", "reason", "change_summary"],
        },
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True),
    ),
    Tool(
        name="list_data_flow_snapshots",
        description="Lists every named state saved in this data flow's history, oldest first.",
        inputSchema={
            "type": "object",
            "properties": {
                "data_flow_name": {"type": "string"},
            },
            "required": ["data_flow_name"],
        },
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
    ),
    Tool(
        name="rollback_data_flow_definition",
        description=(
            "Jumps directly to a specific named state from list_data_flow_snapshots, wherever it sits in "
            "history — nothing is ever deleted. For simple one-step undo/redo without needing a state_name, "
            "use back_data_flow_definition / forward_data_flow_definition instead. If the target state "
            "predates the data flow's creation, this returns requires_confirmation instead of deleting — "
            "ask the human, then re-call with confirm_delete=true."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "data_flow_name": {"type": "string"},
                "reason": {
                    "type": "string",
                    "description": "Why the change is being reverted — shown in the approval dialog.",
                },
                "state_name": {
                    "type": "string",
                    "description": "Name of the state to jump to, from list_data_flow_snapshots.",
                },
                "confirm_delete": {
                    "type": "boolean",
                    "description": "Only set true after the human has explicitly agreed to delete the "
                                    "live data flow, in response to a prior requires_confirmation result.",
                },
            },
            "required": ["data_flow_name", "reason", "state_name"],
        },
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=True),
    ),
    Tool(
        name="back_data_flow_definition",
        description=(
            "Steps one checkpoint back through this data flow's history — see back_pipeline_definition for "
            "the full behavior."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "data_flow_name": {"type": "string"},
                "reason": {
                    "type": "string",
                    "description": "Why the change is being reverted — shown in the approval dialog.",
                },
                "confirm_delete": {
                    "type": "boolean",
                    "description": "Only set true after the human has explicitly agreed to delete the "
                                    "live data flow, in response to a prior requires_confirmation result.",
                },
            },
            "required": ["data_flow_name", "reason"],
        },
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=True),
    ),
    Tool(
        name="forward_data_flow_definition",
        description=(
            "Steps one checkpoint forward through this data flow's history — see forward_pipeline_definition "
            "for the full behavior."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "data_flow_name": {"type": "string"},
                "reason": {
                    "type": "string",
                    "description": "Why the change is being applied — shown in the approval dialog.",
                },
                "confirm_delete": {
                    "type": "boolean",
                    "description": "Only set true after the human has explicitly agreed to delete the "
                                    "live data flow, in response to a prior requires_confirmation result.",
                },
            },
            "required": ["data_flow_name", "reason"],
        },
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=True),
    ),
]
