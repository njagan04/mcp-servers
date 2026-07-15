from mcp.types import Tool, ToolAnnotations

TOOLS = [
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
        name="get_trigger_run_history",
        description=(
            "Trigger-run history — distinct from pipeline-run history. For tumbling-window/event "
            "triggers, the trigger run itself (not the pipeline run it invokes) can fail/rerun/cancel."
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
