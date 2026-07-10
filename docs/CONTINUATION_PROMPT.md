# Continuation prompt — paste this into a new session

I'm continuing R&D on an ADF (Azure Data Factory) self-remediation MCP server for Claude Desktop. The prior chat history for this work was lost, so treat this file plus the code as the source of truth — don't assume anything not stated here or verifiable in the files.

**Scope — read this first:** Work ONLY inside this `claude-desktop/` folder. Do NOT look at or modify the sibling production repo (`../src/`, `../xyz/BUILD_STATUS.md`, `../xyz/implementation_plan.md`) — that's a separate, currently-paused LangGraph workflow project and is explicitly out of scope for this thread.

## What this project is

A standalone, self-contained MCP stdio server (`mcp_adf/`) exposing Azure Data Factory diagnostic + remediation tools to Claude Desktop directly — no LangGraph, no RBAC gateway, no Redis. Own `.venv`, own `.env` (real ADF service-principal creds, gitignored), own `requirements.txt` (`mcp==1.28.1`, `azure-mgmt-datafactory==6.1.0`, `azure-identity==1.25.3`, `azure-core==1.41.0`, `python-dotenv==1.2.2`). Credentials load once from `.env` into a `_CREDS` dict merged into every tool call in `server.py` (`call_tool`) — intentional test-harness shortcut, not meant to mirror any RBAC-gated production pattern.

**Goal:** find out whether Claude, given the right MCP tools, can not just diagnose ADF pipeline failures but propose a concrete fix, get it approved via Claude Desktop's native tool-approval dialog, apply it, and verify it worked.

**Full design log:** `docs/TEST_ADF_CONTEXT.md` — read this in full before doing anything. It has 8 sections covering the SDK surface audit, error-category feasibility, the approval-design reasoning, and (§7–8) the build history for the mutating tools. This prompt only summarizes it.

## Current implementation status (verified by reading the code directly, 2026-07-08)

`mcp_adf/tools.py` + `mcp_adf/server.py` — **15 tools**, all registered in `TOOL_REGISTRY` and all schema'd in `server.py`'s `_TOOLS` list:

Read-only (8): `list_pipelines`, `get_activity_run_error`, `get_pipeline_run_status`, `get_pipeline_run_history`, `get_activity_run_history`, `get_pipeline_definition`, `get_linked_service`, `get_integration_runtime_status`, `get_trigger`, `get_pipeline_definition_raw` — *(that's actually 10, not 8 — recount before trusting either number, verify against `TOOL_REGISTRY` directly)*

Mutating (5): `rerun_pipeline`, `start_integration_runtime`, `start_trigger`, `update_pipeline_definition`, `rollback_pipeline_definition`. Every one of these has a required `reason` argument (schema-enforced), so it surfaces inside Claude Desktop's native approval dialog. `update_pipeline_definition` additionally requires `change_summary`.

MCP `ToolAnnotations` (`readOnlyHint`/`destructiveHint`/`idempotentHint`/`openWorldHint`) are set on all 15 tool definitions in `server.py`.

**Rollback mechanism:** `update_pipeline_definition` snapshots the pre-change pipeline definition to `mcp_adf/_snapshots/<factory>__<pipeline>.json` before overwriting (one level of undo only, consumed/deleted on use by `rollback_pipeline_definition`). `_snapshots/` doesn't exist on disk yet — it's created on first call.

**Confirmed SDK constraint:** `azure-mgmt-datafactory` has no partial-patch API for pipelines — `create_or_update` replaces the entire activities array, so any activity-level fix means: `get_pipeline_definition_raw` → Claude edits the full JSON → `update_pipeline_definition`. Also no validate/dry-run API — "verify" can only mean apply → `rerun_pipeline` → poll `get_pipeline_run_status` → `get_activity_run_error` to confirm the leaf failure cleared.

**Confirmed SDK constraint on IR:** `integration_runtimes.begin_start`/`begin_stop` only works on Azure-SSIS (ManagedReserved) IRs. Self-hosted IRs have no remote-start API anywhere in this SDK — an offline self-hosted IR is human-only, full stop. `get_integration_runtime_status` should always be called first to determine which case applies before ever calling `start_integration_runtime`.

## Known gap — found while reconstructing this context, not yet fixed

`README.md`'s structure section documents a `logs/` folder ("audit log of mutating tool calls, one dated folder per day"), but **no logging code exists anywhere in `mcp_adf/server.py` or `tools.py`**. This was either planned-but-never-built or lost with the chat history. Needs a decision: build it, or correct the README to stop claiming it exists.

## Explicitly not done yet (per docs §8, still true)

- No live test against a real ADF factory (needs real credentials in `.env`)
- No end-to-end test of the actual Wait-activity-insertion flow in Claude Desktop (the whole point of `get_pipeline_definition_raw` + `update_pipeline_definition`)
- Recommended build/test order from docs §7 was: (1) IR start/status, (2) trigger start/status, (3) definition-edit tools — all three are now *coded*, but none verified live

## What to do

Ask me what to work on before assuming — likely candidates are: wire up the missing audit logging, do the first live test against a real factory, or run the Wait-activity end-to-end demo in Claude Desktop. Whatever we do, keep `docs/TEST_ADF_CONTEXT.md` updated as the living log (append, don't rewrite history) so this loss doesn't repeat.
