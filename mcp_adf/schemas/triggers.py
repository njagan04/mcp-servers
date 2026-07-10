from mcp.types import Tool, ToolAnnotations

TOOLS = [
    Tool(
        name="create_trigger",
        description=(
            "Creates a brand-new trigger (e.g. a ScheduleTrigger). Fails with an explicit error if a "
            "trigger with this name already exists — use update_trigger_definition to modify an existing "
            "one instead. Created in a Stopped state, same as ADF Studio's default — call start_trigger "
            "separately once you've verified it. Pushes a \"did not exist\" marker onto this trigger's "
            "history stack, so rollback_trigger_definition can undo the creation (delete it) later."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "trigger_name": {"type": "string"},
                "definition": {
                    "type": "object",
                    "description": "Trigger definition JSON — flat shape (type, typeProperties, pipelines, ...).",
                },
                "reason": {
                    "type": "string",
                    "description": "Why this trigger is being created — shown to the user in the approval dialog.",
                },
                "state_name": {
                    "type": "string",
                    "description": "Optional name for the created state. Defaults to \"created\" if omitted.",
                },
            },
            "required": ["trigger_name", "definition", "reason"],
        },
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True),
    ),
    Tool(
        name="get_trigger",
        description="Get a trigger's runtime state (Started/Stopped/Disabled).",
        inputSchema={
            "type": "object",
            "properties": {
                "trigger_name": {"type": "string"},
            },
            "required": ["trigger_name"],
        },
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
    ),
    Tool(
        name="update_trigger_definition",
        description=(
            "Overwrites a trigger's full definition (e.g. to correct a wrong schedule/recurrence). "
            "`definition` must be get_trigger's raw definition with edits applied. The pre-change "
            "definition is pushed onto this trigger's named history stack automatically, so "
            "rollback_trigger_definition can jump back to it later by name (see list_trigger_snapshots). "
            "Does not change the trigger's Started/Stopped runtime state — use start_trigger/stop_trigger "
            "for that."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "trigger_name": {"type": "string"},
                "definition": {"type": "object", "description": "Modified trigger definition JSON."},
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
            "required": ["trigger_name", "definition", "reason", "change_summary"],
        },
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True),
    ),
    Tool(
        name="list_trigger_snapshots",
        description="Lists every named state saved in this trigger's history, oldest first.",
        inputSchema={
            "type": "object",
            "properties": {
                "trigger_name": {"type": "string"},
            },
            "required": ["trigger_name"],
        },
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
    ),
    Tool(
        name="rollback_trigger_definition",
        description=(
            "Jumps directly to a specific named state from list_trigger_snapshots, wherever it sits in "
            "history — nothing is ever deleted. For simple one-step undo/redo without needing a "
            "state_name, use back_trigger_definition / forward_trigger_definition instead. If the target "
            "state predates the trigger's creation, this returns requires_confirmation instead of "
            "deleting — ask the human, then re-call with confirm_delete=true."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "trigger_name": {"type": "string"},
                "reason": {
                    "type": "string",
                    "description": "Why the change is being reverted — shown in the approval dialog.",
                },
                "state_name": {
                    "type": "string",
                    "description": "Name of the state to jump to, from list_trigger_snapshots.",
                },
                "confirm_delete": {
                    "type": "boolean",
                    "description": "Only set true after the human has explicitly agreed to delete the "
                                    "live trigger, in response to a prior requires_confirmation result.",
                },
            },
            "required": ["trigger_name", "reason", "state_name"],
        },
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=True),
    ),
    Tool(
        name="back_trigger_definition",
        description=(
            "Steps one checkpoint back through this trigger's history — see back_pipeline_definition "
            "for the full behavior."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "trigger_name": {"type": "string"},
                "reason": {
                    "type": "string",
                    "description": "Why the change is being reverted — shown in the approval dialog.",
                },
                "confirm_delete": {
                    "type": "boolean",
                    "description": "Only set true after the human has explicitly agreed to delete the "
                                    "live trigger, in response to a prior requires_confirmation result.",
                },
            },
            "required": ["trigger_name", "reason"],
        },
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=True),
    ),
    Tool(
        name="forward_trigger_definition",
        description=(
            "Steps one checkpoint forward through this trigger's history — see forward_pipeline_definition "
            "for the full behavior."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "trigger_name": {"type": "string"},
                "reason": {
                    "type": "string",
                    "description": "Why the change is being applied — shown in the approval dialog.",
                },
                "confirm_delete": {
                    "type": "boolean",
                    "description": "Only set true after the human has explicitly agreed to delete the "
                                    "live trigger, in response to a prior requires_confirmation result.",
                },
            },
            "required": ["trigger_name", "reason"],
        },
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=True),
    ),
    Tool(
        name="start_trigger",
        description="Starts a stopped or disabled trigger.",
        inputSchema={
            "type": "object",
            "properties": {
                "trigger_name": {"type": "string"},
                "reason": {
                    "type": "string",
                    "description": "Why this fix was chosen — shown to the user in the approval dialog.",
                },
            },
            "required": ["trigger_name", "reason"],
        },
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True),
    ),
    Tool(
        name="stop_trigger",
        description="Stops a running trigger. Inverse of start_trigger — use to pause a misfiring trigger.",
        inputSchema={
            "type": "object",
            "properties": {
                "trigger_name": {"type": "string"},
                "reason": {
                    "type": "string",
                    "description": "Why this fix was chosen — shown to the user in the approval dialog.",
                },
            },
            "required": ["trigger_name", "reason"],
        },
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True),
    ),
    Tool(
        name="list_triggers",
        description="Factory-wide trigger sweep — every trigger's name, type, and runtime state.",
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
        name="get_trigger_run_history",
        description=(
            "Trigger-run history for a specific trigger — distinct from pipeline-run history. "
            "Needed for tumbling-window and event triggers, where the trigger run itself (not the "
            "pipeline run it invokes) is the unit that can fail, be rerun, or be cancelled."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "trigger_name": {"type": "string"},
                "days": {"type": "integer", "default": 7},
            },
            "required": ["trigger_name"],
        },
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
    ),
    Tool(
        name="rerun_trigger_run",
        description=(
            "Reruns a specific trigger run. Needed for tumbling-window/event triggers that don't go "
            "through pipelines.create_run — rerun_pipeline cannot retry these."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "trigger_name": {"type": "string"},
                "trigger_run_id": {"type": "string"},
                "reason": {
                    "type": "string",
                    "description": "Why this fix was chosen — shown to the user in the approval dialog.",
                },
            },
            "required": ["trigger_name", "trigger_run_id", "reason"],
        },
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True),
    ),
    Tool(
        name="cancel_trigger_run",
        description="Cancels a specific in-progress trigger run (tumbling-window/event triggers).",
        inputSchema={
            "type": "object",
            "properties": {
                "trigger_name": {"type": "string"},
                "trigger_run_id": {"type": "string"},
                "reason": {
                    "type": "string",
                    "description": "Why this run is being cancelled — shown in the approval dialog.",
                },
            },
            "required": ["trigger_name", "trigger_run_id", "reason"],
        },
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True),
    ),
]
