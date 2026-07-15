# Nexus ADF MCP Server

**Standalone MCP stdio server exposing Azure Data Factory diagnostic and self-remediation tools to Claude Desktop.**

Given a failing pipeline, it walks Claude through diagnosing the actual root cause (activity errors, run history, raw pipeline/dataset/linked-service/data-flow definitions), proposing a fix, applying it with human approval, and rolling back automatically if verification shows it didn't work.

- 🔍 **Diagnose first** — evidence-driven root-cause analysis before any fix is proposed
- ✅ **Human-in-the-loop** — every mutating call requires explicit approval with a stated reason
- ⏪ **Always reversible** — every checkpoint (pipeline, dataset, linked service, data flow, global parameter) is snapshotted before a mutating change, so nothing destructive is ever unrecoverable
- 📦 **Self-contained** — has its own `.venv` and `.env`, no dependency on any other folder

---

## Contents

- [Setup](#setup)
- [Register with Claude Desktop](#register-with-claude-desktop)
- [Configure permissions in Claude Desktop](#configure-permissions-in-claude-desktop)
- [Instructions](#instructions)
- [Tools](#tools) — 27 tools: 8 generic resource-type tools + 19 kind-specific tools
- [Structure](#structure)

---

## Setup

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

Fill in `.env` with the target factory's service principal credentials:

| Variable | Description |
|---|---|
| `ADF_TENANT_ID` | Azure AD tenant ID |
| `ADF_CLIENT_ID` | Service principal client ID |
| `ADF_CLIENT_SECRET` | Service principal client secret |
| `ADF_SUBSCRIPTION_ID` | Azure subscription ID |
| `ADF_RESOURCE_GROUP` | Resource group containing the factory |
| `ADF_FACTORY_NAME` | Target Data Factory name |

## Register with Claude Desktop

In Claude Desktop's `claude_desktop_config.json`, point at this folder's venv interpreter and `mcp_adf/server.py` (add this alongside your existing config, don't replace it):

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
  ...claude default settings
}
```

> **Note:** Use absolute paths — relative paths are not resolved reliably by Claude Desktop's launcher. `PYTHONPATH` is required so `server.py`'s `from mcp_adf import ...` imports resolve when Claude Desktop launches the script directly (its own cwd isn't this folder).

## Configure permissions in Claude Desktop

After adding the server to `claude_desktop_config.json` and restarting Claude Desktop, go to **Settings → Connectors → nexus-adf** and set:

| Setting | Value | Why |
|---|---|---|
| Read-only tools (`list_*`, `get_*`) | **Always Allow** | Can't mutate anything (`readOnlyHint=True`) — approving per-call adds friction with no safety benefit |
| Write/delete tools (`create_*`, `update_*`, `rerun_*`, `rollback_*`, `back_*`, `forward_*`, `start_*`, `stop_*`, `cancel_*`) | **Ask every time** | Each surfaces a native approval dialog showing the tool's `reason` argument before it runs (see [Instructions](#instructions)) |
| Capabilities → Generate memory from chat history | **Enabled** | Lets Claude carry diagnosis context (e.g. recurring failure patterns for a given pipeline) across sessions |
| Tool access mode | **Tools already loaded** | |

## Instructions

`mcp_adf/server.py`'s `_INSTRUCTIONS` (the MCP `instructions` field passed to the `Server(...)` constructor) is now a one-line pointer, not the full workflow — the full text lives only in the Claude Desktop **project's** instructions field, to avoid sending the same ~1.5k-token block to Claude twice per message. If you're setting this up as a **Claude Desktop Project** (Projects → New Project), paste **both** blocks below into the project's instructions field, and upload the team's SOP doc(s) into the project's context:

<details>
<summary><strong>Show instructions text</strong></summary>

```text
This server exposes Azure Data Factory diagnostic and self-remediation tools. Follow this
workflow for every failure investigation — don't skip straight to a fix, and don't call
tools you don't need.

1. BE ECONOMICAL WITH TOOL CALLS. Every call costs time, and every mutating call is a real
change against a live factory, not a sandbox — call the minimum set needed to reach a
decision, not the maximum available. Before calling a tool, check whether you already have
the answer from earlier in this conversation (a prior call's result, or something the user
already told you) — don't re-call a read-only tool with the same arguments to "double
check" without a reason to distrust the earlier result. If the user already named a specific
resource, go straight to its get_*/get_resource_definition_raw tool — don't call the
matching list_*/list_resources tool first just to browse. Don't call several diagnostic
tools speculatively "in case one has the answer"; decide which one actually answers your
open question and call that. Before create_resource, check existence from what you already
know (a prior list_resources result, or the user's own statement) rather than calling it
speculatively and reacting to an "already_exists" error — that's a wasted round-trip, and
on a mutating tool it still means triggering an approval dialog for something you could
have ruled out in advance.

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
get_resource_definition_raw (resource_type=pipeline/dataset/data_flow/linked_service) for
the real configuration (timeout, query, dataset or linked-service reference, host/port).
Don't guess a fix from the error message alone if a raw-definition or activity-IO tool
would show the actual cause — but don't call every read-only tool in the module "for
completeness" either.

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
place — use back_resource_definition for "undo just this last change" (no state_name
needed). Use rollback_resource_definition instead only when jumping to a specific earlier
named checkpoint (call list_resource_snapshots when you actually intend to roll back, not
preemptively, to see what's available); forward_resource_definition re-applies a change
after stepping back from it. None of these delete history — every checkpoint stays
reachable.

8. When the human says "go back" / "undo" / "revert" for a resource, don't guess which
checkpoint they mean from memory of this conversation alone. Call list_resource_snapshots
first and treat its state_name/reason/change_summary/timestamp fields as the source of
truth for what's actually recoverable, then cross-check that against what you and the human
discussed (e.g. "the change we just made" should match the newest entry's reason/
change_summary) before picking a state_name. If "go back" clearly means "undo the one
change we just made" and the newest entry matches that, back_resource_definition (no
state_name) is simpler and correct; only reach for rollback_resource_definition with an
explicit state_name when the target is an earlier, specifically-named checkpoint, or when
the snapshot list disagrees with what you assumed from conversation context — the snapshot
log is ground truth, your memory of the conversation is not.
```

</details>

<details>
<summary><strong>Show tool-specific nuances</strong> (paste this too — trimmed out of individual tool descriptions to keep the schema payload small)</summary>

```text
Resource-kind nuances (create_resource / update_resource_definition / get_resource_definition_raw):

- create_resource's `definition` accepts either the flat shape get_resource_definition_raw
  returns, or — for resource_type pipeline/dataset/data_flow only — the ARM/Data-Factory-Studio
  export shape ({"name": ..., "properties": {...}}); if a "properties" key is present, its
  contents are used and the wrapper is discarded.
- create_resource(resource_type="trigger") creates the trigger in a Stopped state, same as ADF
  Studio's default — call start_trigger separately once you've verified it.
- create_resource(resource_type="global_parameter")'s `definition` is
  {"type": ..., "value": ...}, not the flat/ARM shapes above.
- get_resource_definition_raw does NOT support resource_type="trigger" — triggers have no
  raw-definition getter (get_trigger returns only name/type/runtime_state). To edit a trigger's
  definition, build the definition JSON yourself (schedule/recurrence/pipelines) and call
  update_resource_definition(resource_type="trigger") directly.
- update_resource_definition(resource_type="trigger") does NOT change the trigger's
  Started/Stopped runtime state — use start_trigger/stop_trigger for that.

Pipeline run/activity diagnostic tools:

- list_pipeline_runs sweeps the whole factory in one call — prefer it over calling
  get_pipeline_run_history once per pipeline when the question is about recent activity
  factory-wide rather than one pipeline's history. It matches ADF Studio's Monitor tab.
- list_activity_runs matches ADF Studio's monitoring view when you drill into a run's
  activity list.
- get_activity_run_io's activity_run_id/run_id are already surfaced by
  get_activity_run_error's leaf/execution_path — call it as a natural follow-up to that,
  not a fresh lookup.
- get_pipeline_definition is activity-graph-only; call get_resource_definition_raw
  (resource_type="pipeline") as part of diagnosis itself, not only once a fix is already
  decided — it's usually the concrete evidence for WHY an activity failed (timeout value,
  actual query, which dataset/linked service it touches).

Integration runtime tools:

- Call get_integration_runtime_status before start_integration_runtime to confirm the
  IR is a managed (Azure/Azure-SSIS) type and is actually Stopped — starting is not
  applicable to self-hosted IRs or to IRs that aren't Stopped.
```

</details>

## Tools

27 tools: 8 generic resource-type tools (each taking a `resource_type` parameter to cover pipelines/datasets/linked services/data flows/triggers/global parameters, wherever the operation is identical across every kind) plus 19 kind-specific tools that don't repeat across kinds (pipeline run/activity diagnostics, trigger start/stop/rerun, `get_linked_service`, integration runtime status/start).

**Type** marks whether a tool mutates anything: read-only tools are safe to Always Allow; mutating tools always require a `reason` argument and surface a native approval dialog (see [Configure permissions](#configure-permissions-in-claude-desktop)).

<details open>
<summary><strong>Generic resource-type tools</strong> (8 tools — cover pipeline/dataset/linked_service/data_flow/trigger/global_parameter)</summary>

Each takes a `resource_type` parameter (one of `pipeline`, `dataset`, `linked_service`, `data_flow`, `trigger`, `global_parameter` — `get_resource_definition_raw` excludes `trigger`, see nuances above) instead of being a separate tool per kind. Replaces what used to be 47 kind-specific tools.

| Tool | Type | What it's for |
|---|---|---|
| `list_resources` | read-only | Factory-wide sweep of every resource of one resource_type — name and light metadata each. |
| `get_resource_definition_raw` | read-only | Full raw definition — the evidence for diagnosis, and the editable input to `update_resource_definition`. |
| `create_resource` | mutating | Create a brand-new resource; fails if the resource_type+name already exists. |
| `update_resource_definition` | mutating | Overwrite a resource's full definition to apply a fix. |
| `list_resource_snapshots` | read-only | List named checkpoints in a resource's history. |
| `rollback_resource_definition` | mutating | Jump to a specific named checkpoint. |
| `back_resource_definition` | mutating | Step one checkpoint back (like `git checkout HEAD~1`). |
| `forward_resource_definition` | mutating | Step one checkpoint forward after a `back_*` call. |

</details>

<details open>
<summary><strong>Pipeline-specific tools</strong> (10 tools)</summary>

| Tool | Type | What it's for |
|---|---|---|
| `get_pipeline_definition` | read-only | Activity graph (names/types/ExecutePipeline refs) only — no timeout/query/dataset detail; use `get_resource_definition_raw` for that. |
| `get_pipeline_run_status` | read-only | Current status of one specific run (freshness check before rerun). |
| `get_pipeline_run_history` | read-only | Recent runs for **one** pipeline. |
| `list_pipeline_runs` | read-only | Recent runs across **the whole factory** in a time window. |
| `get_activity_run_history` | read-only | Aggregated failure counts/last error code per activity across recent runs. |
| `get_activity_run_error` | read-only | Error detail for the most recent failed run of a pipeline — usually the diagnosis starting point. |
| `list_activity_runs` | read-only | Every activity in one specific run (name/type/status/timing/activity_run_id), no input/output payload. |
| `get_activity_run_io` | read-only | Full resolved input/output payload for one specific activity run. |
| `rerun_pipeline` | mutating | Trigger a new run. |
| `cancel_pipeline_run` | mutating | Cancel a running/hung run. |

</details>

<details>
<summary><strong>Trigger-specific tools</strong> (6 tools)</summary>

| Tool | Type | What it's for |
|---|---|---|
| `get_trigger` | read-only | One trigger's runtime state (Started/Stopped/Disabled). |
| `get_trigger_run_history` | read-only | Trigger-run history — needed for tumbling-window/event triggers, where the trigger run (not the pipeline run it invokes) is the unit that fails/reruns/cancels. |
| `start_trigger` | mutating | Start a stopped/disabled trigger. |
| `stop_trigger` | mutating | Stop a running trigger. |
| `rerun_trigger_run` | mutating | Rerun a specific trigger run (tumbling-window/event triggers `rerun_pipeline` can't reach). |
| `cancel_trigger_run` | mutating | Cancel a specific in-progress trigger run. |

</details>

<details>
<summary><strong>Linked-service-specific tools</strong> (1 tool)</summary>

| Tool | Type | What it's for |
|---|---|---|
| `get_linked_service` | read-only | Name and type only — no connection details; use `get_resource_definition_raw` (resource_type="linked_service") for that. |

</details>

<details>
<summary><strong>Integration runtime tools</strong> (2 tools)</summary>

| Tool | Type | What it's for |
|---|---|---|
| `get_integration_runtime_status` | read-only | An IR's state — works for Azure, self-hosted, and Azure-SSIS types. Check before `start_integration_runtime`. |
| `start_integration_runtime` | mutating | Start a stopped **Azure-SSIS (managed)** IR. Does not work on self-hosted IRs — no remote-start API exists; that's human-only. |

</details>

## Structure

```text
mcp_adf/                         the MCP server package
├── server.py                    MCP entrypoint, stdio transport, tool dispatch
├── auth.py                      service-principal auth / ADF client construction
├── audit.py                     audit-log writer (project/_logs/)
├── tools/                       tool implementations, one module per ADF resource kind
│   ├── _shared.py               shared helpers (client construction, wire-dict conversion, miscased-key checks)
│   ├── _checkpoints.py          snapshot/rollback/back/forward engine, shared by every resource kind
│   ├── _dispatch.py             resource_type -> per-kind-function routers backing the 8 generic tools
│   ├── pipelines.py             pipeline implementations — list/get/create/update/rerun/cancel, snapshot/rollback/back/forward
│   ├── triggers.py              trigger implementations — list/get/start/stop/rerun/cancel, run history
│   ├── linked_services.py       linked-service implementations — list/get/update, snapshot/rollback/back/forward
│   ├── datasets.py              dataset implementations — list/get/update, snapshot/rollback/back/forward
│   ├── data_flows.py            data-flow implementations — get/update, snapshot/rollback/back/forward
│   ├── global_parameters.py     global-parameter implementations — list/get/update, snapshot/rollback/back/forward
│   ├── integration_runtimes.py  integration-runtime implementations — get status/start
│   └── __init__.py              assembles TOOL_REGISTRY: kind-specific entries + _dispatch's 8 generic entries
├── schemas/                     MCP tool-schema definitions
│   ├── _generic.py              the 8 resource_type-parameterized tool schemas (dispatched by tools/_dispatch.py)
│   ├── pipelines.py             schema for pipeline-specific tools
│   ├── triggers.py              schema for trigger-specific tools
│   ├── linked_services.py       schema for get_linked_service
│   ├── integration_runtimes.py  schema for integration-runtime tools
│   └── __init__.py              assembles the schema list server.py exposes via list_tools()
│                                 (no datasets.py/data_flows.py/global_parameters.py here — every
│                                  operation for those 3 kinds is covered by schemas/_generic.py)
│
tests/                           unittest suite — no pytest dependency required
├── test_dispatch.py             tools/_dispatch.py routing, kwarg-remapping, unknown-resource_type rejection
├── test_schema_registry_sync.py schema<->TOOL_REGISTRY name parity, generic tool enum<->dispatch-table parity
└── test_call_tool_integration.py end-to-end through server.call_tool() (Azure SDK client faked, audit
                                    log + checkpoint/snapshot system run for real against a temp dir)

project/
    ├── _snapshot/               pre-change pipeline/dataset/data-flow definitions, for rollback (gitignored, created at runtime)
    └── _logs/                   audit log of tool calls, one dated folder per day (gitignored, created at runtime)

docs/TEST_ADF_CONTEXT.md         living design/decision doc for this R&D effort — read before making changes
.env                             real credentials (gitignored, never commit)
.env.example                     template — copy to .env
requirements.txt                 pinned dependencies
```
