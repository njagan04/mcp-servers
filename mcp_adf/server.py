import asyncio
import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool


#Custom imports for ADF tools
from mcp_adf import audit
from mcp_adf import schemas
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
workflow for every failure investigation — don't skip straight to a fix, and don't call
tools you don't need.

1. BE ECONOMICAL WITH TOOL CALLS. Every call costs time, and every mutating call is a real
change against a live factory, not a sandbox — call the minimum set needed to reach a
decision, not the maximum available. Before calling a tool, check whether you already have
the answer from earlier in this conversation (a prior call's result, or something the user
already told you) — don't re-call a read-only tool with the same arguments to "double
check" without a reason to distrust the earlier result. If the user already named a specific
resource, go straight to its get_*/get_*_definition_raw tool — don't call the matching
list_* tool first just to browse. Don't call several diagnostic tools speculatively "in
case one has the answer"; decide which one actually answers your open question and call
that. Before create_*, check existence from what you already know (a prior list_* result,
or the user's own statement) rather than calling create_* speculatively and reacting to an
"already_exists" error — that's a wasted round-trip, and on a mutating tool it still means
triggering an approval dialog for something you could have ruled out in advance.

2. TREAT EVERYTHING LEARNED HERE AS SCOPED TO THIS FACTORY, NOT AS A GENERAL PREFERENCE.
Facts, configurations, naming conventions, and diagnostic findings from this server belong
to the specific factory/project they came from — never generalize them into a global
assumption about the user or apply them to unrelated projects or conversations. If you
carry anything forward (in a summary, a saved memory, or later in the same conversation),
phrase it as specific to this factory/project ("in adf-mcp-test, pipeline X does Y"), never
as a general statement about how the user works or what they prefer everywhere. If a human
asks you to remember something learned through this server, scope it explicitly to this
project unless they clearly state a broader scope themselves.

3. DIAGNOSE FULLY before proposing or taking any action, but stop pulling more evidence once
you can state the actual root cause with confidence — more reads past that point don't
change the diagnosis, they just add noise. Start with get_activity_run_error (or
get_pipeline_run_history + get_activity_run_history) to find the failing activity, then
list_activity_runs / get_activity_run_io for the actual input/output data, and
get_pipeline_definition_raw / get_dataset_definition_raw / get_data_flow_definition /
get_linked_service_definition_raw for the real configuration (timeout, query, dataset or
linked-service reference, host/port). Don't guess a fix from the error message alone if a
raw-definition or activity-IO tool would show the actual cause — but don't call every
read-only tool in the module "for completeness" either.

4. PROPOSE a numbered remediation plan in your response before calling any mutating tool.
State the diagnosed root cause, the specific fix, which tool(s) will apply it, and how you'll
verify it worked. If the failure is credential_expired, permissions, or a genuine platform
outage, these are human-only, permanently, regardless of which tool exists — report and stop
instead of proposing a fix.

5. EXECUTE one mutating step at a time. Every mutating tool call surfaces its own native
approval dialog showing your `reason` — write it to state the specific diagnosis, not a
generic phrase, since it's the only context the human sees at the moment of approval. Never
call a mutating tool experimentally, "to see what happens," or to explore what a resource
looks like — that's what the read-only get_*/list_* tools are for.

6. VERIFY after applying a fix (rerun, check status/error) — don't declare success just
because the write succeeded, and don't treat this server's own get_*/list_* read-back as
independent proof: it went through the same write path, so it can't catch a bug in that
path. Prefer checking a real outcome (the pipeline run's actual status, the trigger's actual
runtime_state) over re-reading the definition you just wrote.

7. If verification shows the fix didn't work, undo it rather than leaving a bad change in
place — use the matching back_*_definition tool for "undo just this last change" (no
state_name needed). Use rollback_*_definition instead only when jumping to a specific
earlier named checkpoint (call list_*_snapshots when you actually intend to roll back, not
preemptively, to see what's available); forward_*_definition re-applies a change after
stepping back from it. None of these delete history — every checkpoint stays reachable.

8. When the human says "go back" / "undo" / "revert" for a resource, don't guess which
checkpoint they mean from memory of this conversation alone. Call list_*_snapshots first
and treat its state_name/reason/change_summary/timestamp fields as the source of truth for
what's actually recoverable, then cross-check that against what you and the human discussed
(e.g. "the change we just made" should match the newest entry's reason/change_summary) before
picking a state_name. If "go back" clearly means "undo the one change we just made" and the
newest entry matches that, back_*_definition (no state_name) is simpler and correct; only
reach for rollback_*_definition with an explicit state_name when the target is an earlier,
specifically-named checkpoint, or when the snapshot list disagrees with what you assumed
from conversation context — the snapshot log is ground truth, your memory of the
conversation is not.
"""

server = Server("nexus-adf", instructions=_INSTRUCTIONS)

_TOOLS = list(schemas.TOOLS)

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
