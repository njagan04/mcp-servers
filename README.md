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
- [Tools](#tools) — 66 tools across 7 categories
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

The server ships its own instructions to Claude automatically (`mcp_adf/server.py`'s `_INSTRUCTIONS`, passed to the `Server(...)` constructor) — no manual copy-paste is needed for the MCP connection itself.

If you're instead setting this up as a **Claude Desktop Project** (Projects → New Project), paste the same instructions into the project's instructions field so Claude follows the diagnose → propose → execute → verify workflow there too, and upload the team's SOP doc(s) into the project's context:

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
```

</details>

## Tools

66 tools, grouped by resource type. **Type** marks whether a tool mutates anything: read-only tools are safe to Always Allow; mutating tools always require a `reason` argument and surface a native approval dialog (see [Configure permissions](#configure-permissions-in-claude-desktop)).

<details open>
<summary><strong>Pipelines</strong> (18 tools)</summary>

| Tool | Type | What it's for |
|---|---|---|
| `list_pipelines` | read-only | List all pipeline names in the factory. |
| `get_pipeline_definition` | read-only | Activity graph (names/types/ExecutePipeline refs) only — no timeout/query/dataset detail. |
| `get_pipeline_definition_raw` | read-only | Full definition — typeProperties, queries, dataset/linked-service refs, timeout/retry policy. The evidence tool for diagnosis, and the editable input to `update_pipeline_definition`. |
| `get_pipeline_run_status` | read-only | Current status of one specific run (freshness check before rerun). |
| `get_pipeline_run_history` | read-only | Recent runs for **one** pipeline. |
| `list_pipeline_runs` | read-only | Recent runs across **the whole factory** in a time window — matches ADF Studio's Monitor tab ("last 24 hours"). |
| `get_activity_run_history` | read-only | Aggregated failure counts/last error code per activity across recent runs. |
| `get_activity_run_error` | read-only | Error detail for the most recent failed run of a pipeline — usually the diagnosis starting point. |
| `list_activity_runs` | read-only | Every activity in one specific run (name/type/status/timing/activity_run_id), no input/output payload. |
| `get_activity_run_io` | read-only | Full resolved input/output payload for one specific activity run. |
| `create_pipeline` | mutating | Create a brand-new pipeline; fails if the name already exists. |
| `update_pipeline_definition` | mutating | Overwrite a pipeline's full definition to apply a fix. |
| `rerun_pipeline` | mutating | Trigger a new run. |
| `cancel_pipeline_run` | mutating | Cancel a running/hung run. |
| `list_pipeline_snapshots` | read-only | List named checkpoints in a pipeline's history. |
| `rollback_pipeline_definition` | mutating | Jump to a specific named checkpoint. |
| `back_pipeline_definition` | mutating | Step one checkpoint back (like `git checkout HEAD~1`). |
| `forward_pipeline_definition` | mutating | Step one checkpoint forward after a `back_*` call. |

</details>

<details>
<summary><strong>Triggers</strong> (13 tools)</summary>

| Tool | Type | What it's for |
|---|---|---|
| `list_triggers` | read-only | Factory-wide sweep — every trigger's name, type, runtime state. |
| `get_trigger` | read-only | One trigger's runtime state (Started/Stopped/Disabled). |
| `get_trigger_run_history` | read-only | Trigger-run history — needed for tumbling-window/event triggers, where the trigger run (not the pipeline run it invokes) is the unit that fails/reruns/cancels. |
| `create_trigger` | mutating | Create a brand-new trigger (e.g. a ScheduleTrigger); fails if the name already exists. Created Stopped. |
| `update_trigger_definition` | mutating | Overwrite a trigger's full definition to apply a fix (e.g. correct a schedule). |
| `start_trigger` | mutating | Start a stopped/disabled trigger. |
| `stop_trigger` | mutating | Stop a running trigger. |
| `rerun_trigger_run` | mutating | Rerun a specific trigger run (tumbling-window/event triggers `rerun_pipeline` can't reach). |
| `cancel_trigger_run` | mutating | Cancel a specific in-progress trigger run. |
| `list_trigger_snapshots` | read-only | List named checkpoints in a trigger's history. |
| `rollback_trigger_definition` | mutating | Jump to a specific named checkpoint. |
| `back_trigger_definition` | mutating | Step one checkpoint back. |
| `forward_trigger_definition` | mutating | Step one checkpoint forward. |

</details>

<details>
<summary><strong>Linked services</strong> (9 tools)</summary>

| Tool | Type | What it's for |
|---|---|---|
| `list_linked_services` | read-only | Factory-wide sweep of linked services. |
| `get_linked_service` | read-only | Name and type only — no connection details. |
| `get_linked_service_definition_raw` | read-only | Full definition including the real host/port/connection string. Diagnosis tool for network/config failures, and the editable input to `update_linked_service_definition`. |
| `create_linked_service` | mutating | Create a brand-new linked service; fails if the name already exists. |
| `update_linked_service_definition` | mutating | Overwrite a linked service's full definition (e.g. fix a wrong host/port). |
| `list_linked_service_snapshots` | read-only | List named checkpoints in a linked service's history. |
| `rollback_linked_service_definition` | mutating | Jump to a specific named checkpoint. |
| `back_linked_service_definition` | mutating | Step one checkpoint back. |
| `forward_linked_service_definition` | mutating | Step one checkpoint forward. |

</details>

<details>
<summary><strong>Datasets</strong> (8 tools)</summary>

| Tool | Type | What it's for |
|---|---|---|
| `list_datasets` | read-only | Factory-wide sweep — name, type, backing linked service. |
| `create_dataset` | mutating | Create a brand-new dataset; fails if the name already exists. |
| `get_dataset_definition_raw` | read-only | Full definition (schema, structure, linked-service ref, parameters) — the evidence for schema-drift diagnosis, and the editable input to `update_dataset_definition`. |
| `update_dataset_definition` | mutating | Overwrite a dataset's full definition (e.g. correct a drifted schema). |
| `list_dataset_snapshots` | read-only | List named checkpoints in a dataset's history. |
| `rollback_dataset_definition` | mutating | Jump to a specific named checkpoint. |
| `back_dataset_definition` | mutating | Step one checkpoint back. |
| `forward_dataset_definition` | mutating | Step one checkpoint forward. |

</details>

<details>
<summary><strong>Data flows</strong> (8 tools)</summary>

| Tool | Type | What it's for |
|---|---|---|
| `list_data_flows` | read-only | Factory-wide sweep — name and type (e.g. MappingDataFlow) for each. |
| `create_data_flow` | mutating | Create a brand-new data flow; fails if the name already exists. |
| `get_data_flow_definition` | read-only | Full Mapping Data Flow definition (sources, sinks, transformation script) — the only way to see inside the transformation graph itself. |
| `update_data_flow_definition` | mutating | Overwrite a data flow's full definition to apply a fix. |
| `list_data_flow_snapshots` | read-only | List named checkpoints in a data flow's history. |
| `rollback_data_flow_definition` | mutating | Jump to a specific named checkpoint. |
| `back_data_flow_definition` | mutating | Step one checkpoint back. |
| `forward_data_flow_definition` | mutating | Step one checkpoint forward. |

</details>

<details>
<summary><strong>Global parameters</strong> (8 tools)</summary>

ADF allows only one Global Parameters resource per factory (name is always `"default"`) —
every parameter is a key inside its `properties` dict, not its own resource. These tools
handle that transparently: "creating"/"removing" one parameter really reads the whole set,
patches one key in/out, and writes the full set back, so siblings are never affected.

| Tool | Type | What it's for |
|---|---|---|
| `list_global_parameters` | read-only | Factory-wide sweep — name, type, value of every global parameter. |
| `create_global_parameter` | mutating | Add a brand-new global parameter; fails if the name already exists. |
| `get_global_parameter_definition_raw` | read-only | Full `{"type", "value"}` definition — the editable input to `update_global_parameter_definition`. |
| `update_global_parameter_definition` | mutating | Overwrite a global parameter's type/value (e.g. fix a stale connection string or flipped env flag). |
| `list_global_parameter_snapshots` | read-only | List named checkpoints in a global parameter's history. |
| `rollback_global_parameter_definition` | mutating | Jump to a specific named checkpoint. |
| `back_global_parameter_definition` | mutating | Step one checkpoint back. |
| `forward_global_parameter_definition` | mutating | Step one checkpoint forward. |

</details>

<details>
<summary><strong>Integration runtimes</strong> (2 tools)</summary>

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
│   ├── pipelines.py             pipeline tools — list/get/create/update/rerun/cancel, snapshot/rollback/back/forward
│   ├── triggers.py              trigger tools — list/get/start/stop/rerun/cancel, run history
│   ├── linked_services.py       linked-service tools — list/get/update, snapshot/rollback/back/forward
│   ├── datasets.py              dataset tools — list/get/update, snapshot/rollback/back/forward
│   ├── data_flows.py            data-flow tools — get/update, snapshot/rollback/back/forward
│   ├── global_parameters.py     global-parameter tools — list/get/update, snapshot/rollback/back/forward
│   ├── integration_runtimes.py  integration-runtime tools — get status/start
│   └── __init__.py              assembles TOOL_REGISTRY from the modules above
├── schemas/                     MCP tool-schema definitions, mirroring tools/ by resource kind
│   ├── pipelines.py             schema for pipeline tools
│   ├── triggers.py              schema for trigger tools
│   ├── linked_services.py       schema for linked-service tools
│   ├── datasets.py              schema for dataset tools
│   ├── data_flows.py            schema for data-flow tools
│   ├── global_parameters.py     schema for global-parameter tools
│   ├── integration_runtimes.py  schema for integration-runtime tools
│   └── __init__.py              assembles the schema list server.py exposes via list_tools()
│
project/
    ├── _snapshot/               pre-change pipeline/dataset/data-flow definitions, for rollback (gitignored, created at runtime)
    └── _logs/                   audit log of tool calls, one dated folder per day (gitignored, created at runtime)

docs/TEST_ADF_CONTEXT.md         living design/decision doc for this R&D effort — read before making changes
.env                             real credentials (gitignored, never commit)
.env.example                     template — copy to .env
requirements.txt                 pinned dependencies
```
