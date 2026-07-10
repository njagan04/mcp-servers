from mcp.types import Tool, ToolAnnotations

TOOLS = [
    Tool(
        name="list_pipelines",
        description="List all pipelines in the data factory.",
        inputSchema={
            "type": "object",
            "properties": {
                "factory_name":{"type": "string"}
            },
            "required":["factory_name"],
        },
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
    ),
    Tool(
        name="create_pipeline",
        description=(
            "Creates a brand-new pipeline. Fails with an explicit error if a pipeline with this "
            "name already exists — use update_pipeline_definition to modify an existing one instead. "
            "Pushes a \"did not exist\" marker onto this pipeline's history stack, so "
            "rollback_pipeline_definition can undo the creation (delete it) later. `definition` accepts "
            "either the flat shape get_pipeline_definition_raw uses, or the ARM/Data-Factory-Studio "
            "export shape ({\"name\": ..., \"properties\": {\"activities\": [...], ...}}) — if a "
            "\"properties\" key is present, its contents are used and the wrapper is discarded. "
            "`pipeline_name` (not the JSON's own \"name\" field, if present) determines the actual "
            "name created."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "pipeline_name": {"type": "string"},
                "definition": {
                    "type": "object",
                    "description": "Pipeline definition JSON — flat shape or {\"name\":..., \"properties\":{...}}.",
                },
                "reason": {
                    "type": "string",
                    "description": "Why this pipeline is being created — shown to the user in the approval dialog.",
                },
                "state_name": {
                    "type": "string",
                    "description": "Optional name for the created state. Defaults to \"created\" if omitted.",
                },
            },
            "required": ["pipeline_name", "definition", "reason"],
        },
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True),
    ),
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
            "Lists every activity in a specific pipeline run — name, type, status, "
            "timing, and activity_run_id for each. Matches ADF Studio's monitoring view "
            "when you drill into a run's activity list. Deliberately lightweight: does "
            "NOT include input/output (can be large) — call get_activity_run_io with a "
            "specific activity_run_id from this list for full input/output on just the "
            "activities that matter."
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
            "Raw input/output payload for one specific activity run — not aggregated "
            "(get_activity_run_history), not just the error (get_activity_run_error), but "
            "the actual resolved input parameters and captured output ADF recorded for "
            "that exact execution. Often the concrete evidence a bare error message "
            "doesn't show: the real query a Copy/Lookup activity ran after "
            "parameter/expression substitution, rows affected, or a Lookup's actual "
            "returned data. Both activity_run_id and run_id are already surfaced by "
            "get_activity_run_error's leaf/execution_path — call this as a natural "
            "follow-up, not a fresh lookup."
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
        description=(
            "Factory-wide run sweep across every pipeline in a time window, matching ADF Studio's "
            "Monitor tab (e.g. \"last 24 hours\"). Use this instead of calling get_pipeline_run_history "
            "once per pipeline when the question is about recent activity across the whole factory "
            "rather than one specific pipeline's history."
        ),
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
            "Fetch the pipeline definition (activity graph) — activity names, types, and "
            "ExecutePipeline references only. For a failing activity's actual timeout policy, "
            "the dataset/linked service it reads or writes, or a query/expression it runs "
            "(the fields that usually explain WHY it failed), use get_pipeline_definition_raw "
            "instead — call it as part of diagnosis, not only when preparing a fix."
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
        name="get_pipeline_definition_raw",
        description=(
            "Full pipeline definition JSON — every activity's typeProperties (dataset/linked-service "
            "references via inputs/outputs/linkedServiceName, queries like sqlReaderQuery, source/sink "
            "settings), policy (timeout, retry, retryIntervalInSeconds), and dependsOn. "
            "Use this during DIAGNOSIS, not just before writing a fix: after get_activity_run_error "
            "identifies the failing activity, call this to see its actual timeout value, the query it "
            "ran, or which dataset/linked service it touches — usually the concrete evidence for WHY it "
            "failed (timeout too short, wrong query, wrong dataset). It's also the editable structure "
            "required as input to update_pipeline_definition once a fix is decided."
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
        name="update_pipeline_definition",
        description=(
            "Overwrites a pipeline's full definition to apply a concrete fix (e.g. inserting a Wait activity, "
            "adjusting a timeout/retry policy). ADF has no partial-patch API — this replaces the entire "
            "activities array, so `definition` must be get_pipeline_definition_raw's output with edits applied. "
            "The pre-change definition is pushed onto this pipeline's named history stack automatically, so "
            "rollback_pipeline_definition can jump back to it later by name (see list_pipeline_snapshots)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "pipeline_name": {"type": "string"},
                "definition": {"type": "object", "description": "Modified output of get_pipeline_definition_raw."},
                "reason": {
                    "type": "string",
                    "description": "The diagnosed root cause driving this change — shown in the approval dialog.",
                },
                "change_summary": {
                    "type": "string",
                    "description": "One-line human-readable diff, e.g. 'insert Wait(60s) between CopyBronze and CopySilver'.",
                },
                "state_name": {
                    "type": "string",
                    "description": "Optional name for the state being saved (what the pipeline looked like before "
                                    "this change). Defaults to a slug of change_summary if omitted.",
                },
            },
            "required": ["pipeline_name", "definition", "reason", "change_summary"],
        },
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True),
    ),
    Tool(
        name="list_pipeline_snapshots",
        description=(
            "Lists every named state saved in this pipeline's history (state_name, timestamp, reason, "
            "change_summary), oldest first. Query this to see what's available before calling "
            "rollback_pipeline_definition with a specific state_name."
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
        name="rollback_pipeline_definition",
        description=(
            "Jumps directly to a specific named state from list_pipeline_snapshots, wherever it sits in "
            "history — nothing is ever deleted, so you can move back and then forward again by name. For "
            "simple one-step undo/redo without needing a state_name, use back_pipeline_definition / "
            "forward_pipeline_definition instead. If the target state predates the pipeline's creation, this "
            "returns requires_confirmation instead of deleting — ask the human, then re-call with "
            "confirm_delete=true."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "pipeline_name": {"type": "string"},
                "reason": {
                    "type": "string",
                    "description": "Why the change is being reverted — shown in the approval dialog.",
                },
                "state_name": {
                    "type": "string",
                    "description": "Name of the state to jump to, from list_pipeline_snapshots.",
                },
                "confirm_delete": {
                    "type": "boolean",
                    "description": "Only set true after the human has explicitly agreed to delete the "
                                    "live pipeline, in response to a prior requires_confirmation result.",
                },
            },
            "required": ["pipeline_name", "reason", "state_name"],
        },
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=True),
    ),
    Tool(
        name="back_pipeline_definition",
        description=(
            "Steps one checkpoint back through this pipeline's history, like `git checkout HEAD~1` — the "
            "history log itself is untouched, only which checkpoint the live pipeline currently matches "
            "moves. Call forward_pipeline_definition to step forward again; repeated back/forward calls walk "
            "the log correctly either direction. If the step lands before the pipeline existed, this returns "
            "requires_confirmation instead of deleting — ask the human, then re-call with confirm_delete=true."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "pipeline_name": {"type": "string"},
                "reason": {
                    "type": "string",
                    "description": "Why the change is being reverted — shown in the approval dialog.",
                },
                "confirm_delete": {
                    "type": "boolean",
                    "description": "Only set true after the human has explicitly agreed to delete the "
                                    "live pipeline, in response to a prior requires_confirmation result.",
                },
            },
            "required": ["pipeline_name", "reason"],
        },
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=True),
    ),
    Tool(
        name="forward_pipeline_definition",
        description=(
            "Steps one checkpoint forward through this pipeline's history — the mirror of "
            "back_pipeline_definition. Only available after a previous back step; returns "
            "no_later_state_available if the cursor is already at the newest checkpoint."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "pipeline_name": {"type": "string"},
                "reason": {
                    "type": "string",
                    "description": "Why the change is being applied — shown in the approval dialog.",
                },
                "confirm_delete": {
                    "type": "boolean",
                    "description": "Only set true after the human has explicitly agreed to delete the "
                                    "live pipeline, in response to a prior requires_confirmation result.",
                },
            },
            "required": ["pipeline_name", "reason"],
        },
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=True),
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
