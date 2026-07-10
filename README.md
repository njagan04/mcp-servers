# Nexus ADF MCP server (Claude Desktop)

Standalone MCP stdio server exposing Azure Data Factory diagnostic and self-remediation tools to Claude Desktop. Given a failing pipeline, it walks Claude through diagnosing the actual root cause (activity errors, run history, raw pipeline/dataset/linked-service/data-flow definitions), proposing a fix, applying it with human approval, and rolling back automatically if verification shows it didn't work. Every checkpoint (pipeline, dataset, linked service, data flow, global parameter) is snapshotted before a mutating change, so nothing destructive is ever unrecoverable. Self-contained — has its own `.venv` and `.env`, no dependency on any other folder.

## Setup

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt

```

Fill in `.env` with the target factory's service principal credentials (`ADF_TENANT_ID`, `ADF_CLIENT_ID`, `ADF_CLIENT_SECRET`, `ADF_SUBSCRIPTION_ID`, `ADF_RESOURCE_GROUP`, `ADF_FACTORY_NAME`).

## Register with Claude Desktop

In Claude Desktop's `claude_desktop_config.json`, point at this folder's venv interpreter and `mcp_adf/server.py`:

```json
{
  "mcpServers": {
    "nexus-adf": {
      "command": "C:\\ABSOLUTE\\PATH\\TO\\THIS\\FOLDER\\.venv\\Scripts\\python.exe",
      "args": ["C:\\ABSOLUTE\\PATH\\TO\\THIS\\FOLDER\\mcp_adf\\server.py"],
      "env": {
        "PYTHONPATH": "C:\\ABSOLUTE\\PATH\\TO\\THIS\\FOLDER"
      }
    }
  },
  ...claude's default settigs
}
```

Use absolute paths — relative paths are not resolved reliably by Claude Desktop's launcher. `PYTHONPATH` is required so `server.py`'s `from mcp_adf import ...` imports resolve when Claude Desktop launches the script directly (its own cwd isn't this folder).

## Configure permissions in Claude Desktop

After adding the server to `claude_desktop_config.json` and restarting Claude Desktop, go to **Settings → Connectors → nexus-adf** and set:

- **Read-only tools** (`list_*`, `get_*`) — **Always Allow**. These can't mutate anything (`readOnlyHint=True`), so approving them per-call adds friction with no safety benefit.
- **Write/delete tools** (`create_*`, `update_*`, `rerun_*`, `rollback_*`, `back_*`, `forward_*`, `start_*`, `stop_*`, `cancel_*`) — leave these on **Ask every time**. Every one of these surfaces a native approval dialog showing the tool's `reason` argument before it runs, per the workflow the server enforces (see [Instructions](#instructions) below).
- Under **Capabilities**, enable **Generate memory from chat history** — lets Claude carry diagnosis context (e.g. recurring failure patterns for a given pipeline) across sessions.
- Set **Tool access mode** to **Tools already loaded**.

## Instructions

The server ships its own instructions to Claude automatically (`mcp_adf/server.py`'s `_INSTRUCTIONS`, passed to the `Server(...)` constructor) — no manual copy-paste is needed for the MCP connection itself.

If you're instead setting this up as a **Claude Desktop Project** (Projects → New Project), paste the same instructions into the project's instructions field so Claude follows the diagnose-propose-execute-verify workflow there too, and upload the team's SOP doc(s) into the project's context:

```
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
```

## Structure

```
mcp_adf/                the MCP server package (server.py, tools.py, auth.py)
project/_snapshots/     pre-change pipeline/dataset/data-flow definitions, for rollback (gitignored, created at runtime)
project/logs/           audit log of tool calls, one dated folder per day (gitignored, created at runtime)
docs/TEST_ADF_CONTEXT.md  living design/decision doc for this R&D effort — read before making changes
.env                    real credentials (gitignored, never commit)
.env.example            template — copy to .env
requirements.txt        pinned dependencies
```
