import asyncio
import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool, ToolAnnotations

from mcp_adf import audit
from mcp_adf import tools as adf_tools

# Load .env from this package's own project root (one level up from mcp_adf/) so the
# folder is self-contained and works when shipped/renamed/moved independently.
load_dotenv(Path(__file__).parents[1] / ".env")

_CREDS = {
    "tenant_id": os.environ["ADF_TENANT_ID"],
    "client_id": os.environ["ADF_CLIENT_ID"],
    "client_secret": os.environ["ADF_CLIENT_SECRET"],
    "subscription_id": os.environ["ADF_SUBSCRIPTION_ID"],
    "resource_group": os.environ["ADF_RESOURCE_GROUP"],
    "factory_name": os.environ["ADF_FACTORY_NAME"],
}

_INSTRUCTIONS = """\
This server exposes Azure Data Factory diagnostic and self-remediation tools. Follow this 
workflow for every failure investigation — don't skip straight to a fix.

1. DIAGNOSE FULLY before proposing or taking any action. Start with get_activity_run_error
(or get_pipeline_run_history + get_activity_run_history) to find the failing activity, then
list_activity_runs / get_activity_run_io for the actual input/output data, and 
get_pipeline_definition_raw / get_dataset_definition_raw / get_data_flow_definition / 
get_linked_service_definition_raw for the real configuration (timeout, query, dataset or
linked-service reference, host/port). Don't guess a fix from the error message alone if a 
raw-definition or activity-IO tool would show the actual cause.

2. PROPOSE a numbered remediation plan in your response before calling any mutating tool. 
State the diagnosed root cause, the specific fix, which tool(s) will apply it, and how you'll 
verify it worked. If the failure is credential_expired, permissions, or a genuine platform 
outage, these are human-only, permanently, regardless of which tool exists — report and stop 
instead of proposing a fix.

3. EXECUTE one mutating step at a time. Every mutating tool call surfaces its own native 
approval dialog showing your `reason` — write it to state the specific diagnosis, not a 
generic phrase, since it's the only context the human sees at the moment of approval.

4. VERIFY after applying a fix (rerun, check status/error) — don't declare success just 
because the write succeeded.

5. If verification shows the fix didn't work, undo it rather than leaving a bad change in
place — use the matching back_*_definition tool for "undo just this last change" (no
state_name needed). Use rollback_*_definition instead only when jumping to a specific
earlier named checkpoint (list_*_snapshots shows what's available); forward_*_definition
re-applies a change after stepping back from it. None of these delete history — every
checkpoint stays reachable.
"""

server = Server("nexus-adf", instructions=_INSTRUCTIONS)

_TOOLS = [
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
    Tool(
        name="list_datasets",
        description="Factory-wide dataset sweep — name, type, and backing linked service for each.",
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
        name="get_dataset_definition_raw",
        description=(
            "Full dataset definition (schema, structure, linked service reference, parameters) — the "
            "evidence needed to diagnose schema_drift. Feed the returned dict back into "
            "update_dataset_definition (with edits applied) to apply a fix."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "dataset_name": {"type": "string"},
            },
            "required": ["dataset_name"],
        },
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
    ),
    Tool(
        name="update_dataset_definition",
        description=(
            "Overwrites a dataset's full definition to apply a concrete fix (e.g. correcting a drifted "
            "schema). `definition` must be get_dataset_definition_raw's output with edits applied. "
            "The pre-change definition is pushed onto this dataset's named history stack automatically, "
            "so rollback_dataset_definition can jump back to it later by name (see list_dataset_snapshots)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "dataset_name": {"type": "string"},
                "definition": {"type": "object", "description": "Modified output of get_dataset_definition_raw."},
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
            "required": ["dataset_name", "definition", "reason", "change_summary"],
        },
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True),
    ),
    Tool(
        name="list_dataset_snapshots",
        description="Lists every named state saved in this dataset's history, oldest first.",
        inputSchema={
            "type": "object",
            "properties": {
                "dataset_name": {"type": "string"},
            },
            "required": ["dataset_name"],
        },
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
    ),
    Tool(
        name="rollback_dataset_definition",
        description=(
            "Jumps directly to a specific named state from list_dataset_snapshots, wherever it sits in "
            "history — nothing is ever deleted. For simple one-step undo/redo without needing a state_name, "
            "use back_dataset_definition / forward_dataset_definition instead. If the target state predates "
            "the dataset's creation, this returns requires_confirmation instead of deleting — ask the human, "
            "then re-call with confirm_delete=true."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "dataset_name": {"type": "string"},
                "reason": {
                    "type": "string",
                    "description": "Why the change is being reverted — shown in the approval dialog.",
                },
                "state_name": {
                    "type": "string",
                    "description": "Name of the state to jump to, from list_dataset_snapshots.",
                },
                "confirm_delete": {
                    "type": "boolean",
                    "description": "Only set true after the human has explicitly agreed to delete the "
                                    "live dataset, in response to a prior requires_confirmation result.",
                },
            },
            "required": ["dataset_name", "reason", "state_name"],
        },
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=True),
    ),
    Tool(
        name="back_dataset_definition",
        description=(
            "Steps one checkpoint back through this dataset's history — see back_pipeline_definition for "
            "the full behavior."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "dataset_name": {"type": "string"},
                "reason": {
                    "type": "string",
                    "description": "Why the change is being reverted — shown in the approval dialog.",
                },
                "confirm_delete": {
                    "type": "boolean",
                    "description": "Only set true after the human has explicitly agreed to delete the "
                                    "live dataset, in response to a prior requires_confirmation result.",
                },
            },
            "required": ["dataset_name", "reason"],
        },
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=True),
    ),
    Tool(
        name="forward_dataset_definition",
        description=(
            "Steps one checkpoint forward through this dataset's history — see forward_pipeline_definition "
            "for the full behavior."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "dataset_name": {"type": "string"},
                "reason": {
                    "type": "string",
                    "description": "Why the change is being applied — shown in the approval dialog.",
                },
                "confirm_delete": {
                    "type": "boolean",
                    "description": "Only set true after the human has explicitly agreed to delete the "
                                    "live dataset, in response to a prior requires_confirmation result.",
                },
            },
            "required": ["dataset_name", "reason"],
        },
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=True),
    ),
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

_TOOL_MAP = adf_tools.TOOL_REGISTRY
_ANNOTATIONS = {t.name: t.annotations for t in _TOOLS}


@server.list_tools()
async def list_tools() -> list[Tool]:
    return _TOOLS


def _log_call_best_effort(*args, **kwargs) -> None:
    """
    Audit logging must never affect the outcome the caller sees. If logging itself fails
    (disk error, a non-JSON-serializable value in `result`, etc.), that failure is swallowed
    here rather than propagating — otherwise a logging fault after a mutating call already
    succeeded would raise back to the caller, making Claude believe the mutation failed and
    potentially retry an already-applied irreversible operation.
    """
    try:
        audit.log_call(*args, **kwargs)
    except Exception as log_exc:
        print(f"[mcp_adf] audit logging failed (tool result unaffected): {log_exc}", file=sys.stderr)


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    fn = _TOOL_MAP.get(name)
    if fn is None:
        raise ValueError(f"Unknown tool: {name}")
    loop = asyncio.get_running_loop()
    merged = {**_CREDS, **arguments}
    mutating = not _ANNOTATIONS[name].readOnlyHint
    start = time.monotonic()
    try:
        result = await loop.run_in_executor(None, lambda: fn(**merged))
    except Exception as exc:
        _log_call_best_effort(name, arguments, mutating=mutating,
                               duration_ms=(time.monotonic() - start) * 1000, error=exc)
        raise
    _log_call_best_effort(name, arguments, mutating=mutating,
                           duration_ms=(time.monotonic() - start) * 1000, result=result)
    return [TextContent(type="text", text=json.dumps(result))]


async def run():
    async with stdio_server() as streams:
        await server.run(*streams, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(run())
