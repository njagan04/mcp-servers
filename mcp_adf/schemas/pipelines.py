from mcp.types import Tool, ToolAnnotations

TOOLS = [
    Tool(
        name="get_activity_run_error",
        description="Fetch activity-level error detail for the most recent failed run of a pipeline.",
        inputSchema={
            "type": "object",
            "properties": {
                "pipeline_name": {"type": "string"},
                "event_timestamp": {"type": "string", "description": "ISO-8601 timestamp from the failure event"},
            },
            "required": ["pipeline_name", "event_timestamp"],
        },
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
    ),
    Tool(
        name="list_activity_runs",
        description=(
            "Lists every activity in a pipeline run — name, type, status, timing, "
            "activity_run_id. Excludes input/output (see get_activity_run_io)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "days": {"type": "integer", "default": 30, "description": "How far back to search for the run."},
            },
            "required": ["run_id"],
        },
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
    ),
    Tool(
        name="get_activity_run_io",
        description=(
            "Raw resolved input/output for one specific activity run (e.g. the real query "
            "run after parameter substitution, rows affected) — not aggregated "
            "(get_activity_run_history) or error-only (get_activity_run_error)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "activity_run_id": {"type": "string"},
                "run_id": {"type": "string", "description": "The parent pipeline run id."},
                "days": {"type": "integer", "default": 30, "description": "How far back to search for the run."},
            },
            "required": ["activity_run_id", "run_id"],
        },
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
    ),
    Tool(
        name="get_pipeline_run_status",
        description="Get current status of a specific pipeline run (used for freshness check before rerun).",
        inputSchema={
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
            },
            "required": ["run_id"],
        },
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
    ),
    Tool(
        name="get_pipeline_run_history",
        description="Fetch recent run history for a pipeline.",
        inputSchema={
            "type": "object",
            "properties": {
                "pipeline_name": {"type": "string"},
                "days": {"type": "integer", "default": 7},
            },
            "required": ["pipeline_name"],
        },
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
    ),
    Tool(
        name="list_pipeline_runs",
        description="Factory-wide pipeline run sweep across a time window (e.g. \"last 24 hours\").",
        inputSchema={
            "type": "object",
            "properties": {
                "factory_name": {"type": "string"},
                "hours": {"type": "integer", "default": 24, "description": "How far back to search, in hours."},
            },
            "required": ["factory_name"],
        },
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
    ),
    Tool(
        name="get_activity_run_history",
        description=(
            "Aggregated summary of which activities have failed in recent runs of a pipeline. "
            "Returns failure counts and last error code per activity — useful for spotting recurring failures."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "pipeline_name": {"type": "string"},
                "days": {"type": "integer", "default": 7},
            },
            "required": ["pipeline_name"],
        },
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
    ),
    Tool(
        name="get_pipeline_definition",
        description=(
            "Activity graph only (names, types, ExecutePipeline refs) — for real failure "
            "evidence (timeout policy, query, dataset/linked-service refs) use "
            "get_resource_definition_raw instead."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "pipeline_name": {"type": "string"},
            },
            "required": ["pipeline_name"],
        },
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
    ),
    Tool(
        name="rerun_pipeline",
        description="Trigger a new run of a pipeline.",
        inputSchema={
            "type": "object",
            "properties": {
                "pipeline_name": {"type": "string"},
                "parameters": {"type": "object"},
                "reason": {
                    "type": "string",
                    "description": "Why this rerun is being triggered — shown to the user in the approval dialog.",
                },
            },
            "required": ["pipeline_name", "reason"],
        },
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True),
    ),
    Tool(
        name="cancel_pipeline_run",
        description=(
            "Cancels a running (or hung) pipeline run. Call this before retrying a fix if a prior "
            "rerun_pipeline call is stuck rather than cleanly failed."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "reason": {
                    "type": "string",
                    "description": "Why this run is being cancelled — shown in the approval dialog.",
                },
            },
            "required": ["run_id", "reason"],
        },
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True),
    ),
]
