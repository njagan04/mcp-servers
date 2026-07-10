# Nexus AI — `test/` R&D Context (ADF MCP Tools)

**Scope note:** Production workflow (`src/`) is currently halted / out of scope. All work described here is confined to `mcp_adf/` — a standalone MCP stdio server used to experiment with Claude Desktop directly (no LangGraph, no RBAC gateway, no Redis credential flow). Credentials in `mcp_adf/server.py` are loaded straight from `.env` into a `_CREDS` dict merged into every tool call — this is a test-harness shortcut and is NOT meant to mirror `src/` (see `xyz/qa.txt`).

We are in **R&D / brainstorming phase**. Nothing below has been implemented yet.

---

## 1. Current state of `mcp_adf/`

Files: `mcp_adf/tools.py`, `mcp_adf/server.py`, `mcp_adf/auth.py`.

`TOOL_REGISTRY` currently exposes 8 tools:
- `list_pipelines`
- `get_activity_run_error` (resolves run_id from pipeline_name + timestamp, follows `ExecutePipeline` chains to the leaf failure, handles parallel-branch fan-out)
- `get_pipeline_run_status`
- `get_pipeline_run_history` (now includes `triggered_by: {name, type}`)
- `get_activity_run_history` (aggregated failure summary per activity; includes `last_failure_type`, `last_error_source`, `last_activity_run_id`)
- `get_pipeline_definition`
- `get_linked_service`
- `rerun_pipeline`

`mcp_adf/` is currently **ahead of** `src/mcp_servers/adf/` (richer error fields, `list_pipelines`) — full diff is in `xyz/qa.txt`. That reconciliation is parked until production work resumes; don't act on it right now.

SDK in use: `azure-mgmt-datafactory==6.1.0` (installed in `.venv`), auth via `azure-identity.ClientSecretCredential` (`mcp_adf/auth.py`).

---

## 2. Goal for this R&D phase

Explore whether Claude (via Claude Desktop + MCP tools against `mcp_adf/`) can not just **diagnose** ADF pipeline failures but **self-remediate** some of them — propose a fix, ask for human approval via Claude Desktop's native tool-approval dialog, then execute.

---

## 3. SDK surface audit — what's unused

`azure-mgmt-datafactory` exposes ~15 operation groups; `mcp_adf/tools.py` only touches `pipelines`, `pipeline_runs`, `activity_runs`, `linked_services`. Everything else is unused surface.

### Read-only / diagnostic (safe, no real risk)
| SDK group | Method | Adds |
|---|---|---|
| `triggers` | `get`, `list_by_factory` | Trigger status (Started/Stopped) — currently invisible to us |
| `trigger_runs` | `query_by_factory` | Trigger-level run history (distinct from pipeline runs) |
| `integration_runtimes` | `get`, `get_status`, `get_monitoring_data` | Self-hosted IR health/online status |
| `linked_services` | `list_by_factory` | Factory-wide sweep vs. today's single-service lookup |
| `datasets` | `get`, `list_by_factory` | Schema/structure, for `schema_drift` diagnosis |
| `factories` | `get` | Factory-level state, useful for `platform_outage` |

### Mutating / action (needed for actual self-healing)
| SDK group | Method | Error category addressed | Risk |
|---|---|---|---|
| `integration_runtimes` | `begin_start` / `begin_stop` | `resource_unavailable` (self-hosted IR offline) — common real-world cause | Low, reversible |
| `triggers` | `begin_start` / `begin_stop` | Not in current `error_categories.py`, but "trigger got disabled" is one of the most common real ADF incidents | Low, reversible |
| `trigger_runs` | `rerun`, `cancel` | Rerun/cancel at trigger-run granularity — needed for tumbling-window/event triggers that don't go through `pipelines.create_run` | Low–medium |
| `pipelines` / `datasets` | `create_or_update` | `config` / `schema_drift` — patching a bad parameter or stale schema | **High** — rewrites the definition, not just a run. Needs diff-preview before ever prototyping. |

**Confirmed gap:** No "test connection" API exists anywhere in `azure-mgmt-datafactory` — that's a Studio/data-plane-only feature. `network`/`storage_access` root-causing stays inferential (error text + run history), never directly verifiable via this SDK.

**Out of scope for this SDK entirely:** `permissions` category fixes need `azure-mgmt-authorization` (RBAC role assignment) — a different SDK, different auth surface. Not an ADF MCP server concern.

---

## 4. Error-category feasibility (from `src/config/error_categories.py`)

| Category | Self-heal feasible? | Notes |
|---|---|---|
| `resource_unavailable` | **Yes — new tool needed** | `start_integration_runtime` |
| *(trigger stopped, not yet a named category)* | **Yes — new tool needed** | `start_trigger`; consider adding as a named category |
| `network`, `rate_limit`, `timeout` | **Yes — already covered** | `rerun_pipeline` (retry) already handles this |
| `config`, `schema_drift`, `business_logic` | Diagnosable, not safely auto-fixable yet | Needs definition rewrite (`create_or_update`) + diff-preview step before prototyping |
| `credential_expired`, `permissions`, `platform_outage` | **No — human-only, permanently** | Claude can never supply a new secret or grant an RBAC role; no tool changes this |
| `oom` | Partial | Could mean bumping DIU/compute on an activity via `create_or_update` — same high-risk bucket as `config` |

**Recommended next prototypes (highest value, lowest risk):** `get_trigger` / `get_integration_runtime_status` (read) paired with `start_integration_runtime` / `start_trigger` (action). Real remediation, low blast radius, easy to demo end-to-end in Claude Desktop.

---

## 5. Approval / permission design — resolved thinking

**Key realization:** Claude Desktop's native per-tool-call permission dialog already **is** the "propose → approve → execute" flow being asked for. Claude proposes a tool call with arguments, the client pauses and shows it, execution only happens after explicit user approval. No custom "ask permission" tool or text-based confirmation step is needed — a fake in-band gate could be reasoned around by the model, whereas the native dialog is enforced by the client, outside the model's control.

Two concrete additions on top of the native mechanism (for whenever we do prototype the mutating tools):

1. **MCP tool annotations** — set `readOnlyHint: true` on all read tools; `destructiveHint: true` (and `idempotentHint` appropriately) on `start_integration_runtime`, `start_trigger`, `rerun_pipeline`, etc. Claude Desktop uses these to shape how the approval dialog looks/warns.
2. **Require a `reason` argument on every mutating tool.** Claude fills it from its own diagnosis (e.g. `"IR nexus-shir-01 has been Offline since 09:42, no runs succeeded since — restarting to clear it"`) before calling the tool. Because it's a required schema field, it shows up **inside** the native approval dialog itself — the human sees the "why" at the moment of approval, not buried in chat scrollback. This is the right place to put "role"-like guidance — as a schema constraint, not a prompt Claude could ignore.

A project-level system prompt is still worth writing later, but for a narrower job than approval: telling Claude **when** it's allowed to even reach for a mutating tool (e.g. "never call a mutating tool for `credential_expired`/`permissions`/`platform_outage` — always report and stop"), not for reimplementing approval logic in prose.

---

## 6. Open questions / not yet decided

- Should `list_pipelines` graduate anywhere, or stay test-only? (Real workflow always has `pipeline_name` from the WatchTower event — only relevant if a human-driven Claude Desktop session needs to browse a factory without a known pipeline name, which is plausible for this R&D use case even if not for the automated `src/` workflow.)
- Whether to add a named `trigger_stopped` error category, or fold it under `resource_unavailable`.
- Whether `create_or_update`-based remediation (config/schema_drift) is ever worth prototyping without a diff-preview mechanism first.
- No decision yet on whether to actually build `start_integration_runtime` / `start_trigger` — this doc captures the analysis, not a commitment to implement.

---

## 7. Gap analysis — current 8 tools vs. the "propose a concrete fix → approve → apply → verify" loop

Reviewed `mcp_adf/tools.py` and `server.py` directly (2026-07-07). Finding: **all 8 current tools are diagnosis-only.** `get_pipeline_definition` returns only `{name, type, references_pipeline}` per activity — no `typeProperties`, `policy` (timeout/retry), or `dependsOn` wiring. That's enough to name the failing activity, but not enough to construct a valid modified pipeline JSON. You cannot propose something concrete like "add a Wait activity between CopyBronze and CopySilver" without the full raw definition to edit first.

**Mechanical constraint:** the SDK has no "add one activity" primitive. `pipelines.create_or_update` replaces the *entire* activities array. So any activity-level fix is: read full definition → Claude constructs the modified JSON (new activity inserted, `dependsOn` rewired so downstream activities point at the new one) → write the whole thing back.

**Also confirmed:** `azure-mgmt-datafactory` has no validate/dry-run API — that's Studio-only (data-plane feature). "Verify" can never mean static validation; it has to mean apply → `rerun_pipeline` → `get_pipeline_run_status` (poll) → `get_activity_run_error` (confirm the leaf failure is gone). If it still fails, that's the trigger for a rollback, not a second guess.

### New tools needed for definition-level fixes (Wait activity, timeout/retry policy changes, etc.)

| Tool | Type | Purpose |
|---|---|---|
| `get_pipeline_definition_raw` | read | Full activity JSON (typeProperties, policy, dependsOn) — the actual editable structure, superset of today's `get_pipeline_definition` |
| `update_pipeline_definition` | **mutating, high-risk** | Takes the full modified definition + required `reason` (diagnosis) + required `change_summary` (one-line human diff, e.g. "insert Wait(60s) between CopyBronze and CopySilver") — both required schema fields so they surface inside Claude Desktop's native approval dialog itself, per the [[approval-design]] already resolved in §5 |
| `rollback_pipeline_definition` | mutating | `update_pipeline_definition` should snapshot the pre-change definition server-side before writing; this tool re-applies the snapshot. Needed for the "if the fix doesn't work, undo" half of the loop |

### Cheaper, lower-risk wins for the same approval pattern — recommended to prototype FIRST

No definition edit needed at all; matches the propose→approve→verify shape with much smaller blast radius:

| SDK group | Tools to add | Category addressed |
|---|---|---|
| `integration_runtimes` | `get_integration_runtime_status` (read) + `start_integration_runtime` (mutating) | self-hosted IR offline (`resource_unavailable`) |
| `triggers` | `get_trigger` (read) + `start_trigger` (mutating) | trigger got disabled |

### Recommended build order (decided 2026-07-07, not yet implemented)

1. `get_integration_runtime_status` + `start_integration_runtime` — smallest surface, first real end-to-end demo of the approval loop
2. `get_trigger` + `start_trigger` — same shape, second most common real-world cause
3. `get_pipeline_definition_raw` + `update_pipeline_definition` + `rollback_pipeline_definition` — the Wait-activity case; higher risk, sequenced after 1–2 prove the approval/verify pattern actually works end-to-end in Claude Desktop

Still permanently out of reach regardless of tooling, per §4: `credential_expired`, `permissions`, `platform_outage` — enforced by *not building a tool* for these, not by prompting Claude to refrain.

**Correction found while implementing (2026-07-08):** `IntegrationRuntimesOperations.begin_start`/`begin_stop` in the installed SDK (`azure-mgmt-datafactory` ~6.1.0, confirmed by reading `_integration_runtimes_operations.py` directly) are documented as starting/stopping a **"ManagedReserved"** (Azure-SSIS) integration runtime only. There is no remote-start API for self-hosted IRs anywhere in this SDK — a self-hosted IR is a Windows service on customer infrastructure; going "Offline" is human-only, full stop, regardless of tooling. §4's `resource_unavailable` row should be read as: self-hosted IR offline → still human-only; Azure-SSIS IR stopped → auto-fixable via `start_integration_runtime`. `get_integration_runtime_status` (read) works for any IR type and should always be called first to tell which case applies.

---

## 8. Implementation status (2026-07-08)

All three tiers from §7's build order are now implemented in `mcp_adf/tools.py` and wired into `mcp_adf/server.py`'s `TOOL_REGISTRY` — 15 tools total (up from 8):

- **New read tools:** `get_integration_runtime_status`, `get_trigger`, `get_pipeline_definition_raw`
- **New mutating tools:** `start_integration_runtime`, `start_trigger`, `update_pipeline_definition`, `rollback_pipeline_definition` — every mutating tool has a required `reason` field (verified programmatically that all mutating tools in the MCP schema require it), so it surfaces inside Claude Desktop's native approval dialog per the §5 design. `update_pipeline_definition` additionally requires `change_summary`.
- **MCP tool annotations added** to all 15 tools (`readOnlyHint`/`destructiveHint`/`idempotentHint`/`openWorldHint`) per the §5 design — confirmed supported by the installed `mcp==1.28.1` SDK (`ToolAnnotations` in `mcp.types`).
- **Rollback mechanism:** `update_pipeline_definition` snapshots the pre-change pipeline definition to `mcp_adf/_snapshots/<factory>__<pipeline>.json` before overwriting. `rollback_pipeline_definition` restores it and deletes the snapshot (consumed on use). Only one level of undo is kept — this is "undo the last change," not a full version history. Claude can call `rollback_pipeline_definition` itself (with a `reason`, subject to the same approval dialog) if a post-fix verify step (`rerun_pipeline` → `get_pipeline_run_status` → `get_activity_run_error`) shows the fix didn't work — or the user can ask for it explicitly.
- `PipelineResource.deserialize()`/`.as_dict()` (confirmed present on the SDK's vendored `_serialization.Model` base, not `msrest`) used for the round-trip between `get_pipeline_definition_raw` and `update_pipeline_definition`/`rollback_pipeline_definition`.
- Verified: both files import cleanly, all 15 schemas in `server.py` match `TOOL_REGISTRY`, no mutating tool schema is missing its required `reason`.

**Not yet done:** no live test against a real ADF factory (need real credentials); no test of the actual Wait-activity insertion end-to-end in Claude Desktop.

---

## 9. Audit logging + cleanup (2026-07-08)

Prior chat history covering this thread was lost; reconstructed from `docs/CONTINUATION_PROMPT.md` plus reading the code directly. Two gaps found and fixed:

- **`rerun_pipeline` falsely claimed RBAC gating.** Its MCP-exposed description (`server.py`) and docstring (`tools.py`) both said "RBAC-gated: requires senior_eng or admin role" — leftover language that doesn't apply here. This standalone server has no RBAC gateway; it authenticates once via the service-principal creds in `.env` (per §0's scope note). The false claim would have surfaced inside Claude Desktop's approval dialog and misrepresented what protection actually exists. Removed from both places.
- **`logs/` audit trail was documented but never implemented.** `README.md` and `.gitignore` referenced a `logs/` folder ("audit log of mutating tool calls, one dated folder per day"), but no logging code existed in `server.py` or `tools.py`. Built `mcp_adf/audit.py`:
  - `log_call(name, arguments, *, mutating, duration_ms, result=None, error=None)` appends one JSON line to `logs/<YYYY-MM-DD>/audit.jsonl`.
  - Hooked into the single choke point every tool call already passes through — `call_tool()` in `server.py` — rather than instrumenting each of the 15 tool functions individually.
  - Logs **every** call (read and mutating), tagged with a `mutating` bool derived from the tool's existing `readOnlyHint` annotation, not just mutating ones — diagnosis calls are what led to a mutation decision, so they're part of the audit trail too.
  - Logs the caller-supplied `arguments` only, never the `merged` dict `call_tool` builds internally (`{**_CREDS, **arguments}`) — credentials never reach disk. Verified live: a real `list_pipelines` call and a real `get_pipeline_definition` failure (`ResourceNotFoundError`) both logged correctly with no `client_secret`/`tenant_id`/etc. in the entry.
  - This also incidentally produced the first-ever live call against the real factory (`adf-mcp-test`) during verification — `list_pipelines` returned `pl_mcp_test`, `pl_mcp_test2`. Not a substitute for the Wait-activity end-to-end demo still pending, but confirms live credentials and connectivity work.

**Still not yet done:** no end-to-end Wait-activity insertion test in Claude Desktop; reconciliation with `src/mcp_servers/adf/` remains parked.

---

## 10. SDK-surface expansion — 15 → 29 tools (2026-07-08)

Re-audited the full `azure-mgmt-datafactory` operation-group/method list (all 23 groups, not just the ones already touched) and diffed against what §3's original audit had already covered. Also checked the latest PyPI release before building anything.

**SDK version check:** installed `azure-mgmt-datafactory==6.1.0`; latest on PyPI is `10.0.0`. Installed the latest into an isolated `--target` dir (not the project `.venv`) purely to inspect it — did **not** upgrade. The operation groups relevant to this project's tools (`pipelines`, `triggers`, `trigger_runs`, `datasets`, `data_flows`, `pipeline_runs`, `linked_services`) have **identical method signatures** across 6.1.0 → 10.0.0. The only new group, `IntegrationRuntimeOperations` (interactive-query enable/disable for Data Flow debug sessions), is infra-level and out of scope. No new "test connection" API appeared either — the doc's §3 "confirmed gap" still holds. Conclusion: no functional reason to take on a 4-major-version upgrade's breaking-change risk. Staying on `6.1.0`.

**Scope decision for this round:** ADF-level tools only (pipelines, triggers, trigger runs, linked services, datasets, data flows) — explicitly excluding infra-level SDK surface (`factories`, `integration_runtime_nodes`, IR monitoring/network-endpoint calls, managed VNets/private endpoints). Those remain unbuilt by choice, not by omission.

**14 new tools added**, `TOOL_REGISTRY` now has 29 entries:

| New tool | SDK call | Closes |
|---|---|---|
| `cancel_pipeline_run` | `pipeline_runs.cancel` | No way to cancel a stuck/hung run before retrying a fix — a verify-loop safety hole |
| `stop_trigger` | `triggers.begin_stop` | `start_trigger` had no inverse — couldn't pause a misfiring trigger |
| `list_triggers` | `triggers.list_by_factory` | Factory-wide trigger sweep vs. today's single-trigger `get_trigger` |
| `get_trigger_run_history` | `trigger_runs.query_by_factory` | Trigger-run history is distinct from pipeline-run history — needed for tumbling-window/event triggers |
| `rerun_trigger_run` | `trigger_runs.rerun` | Tumbling-window/event triggers don't go through `pipelines.create_run`; `rerun_pipeline` can't retry these |
| `cancel_trigger_run` | `trigger_runs.cancel` | Same category, cancel side |
| `list_linked_services` | `linked_services.list_by_factory` | Factory-wide sweep vs. today's single-service `get_linked_service` — useful when the failing LS isn't named up front |
| `list_datasets` | `datasets.list_by_factory` | Factory-wide dataset sweep |
| `get_dataset_definition_raw` | `datasets.get` | **Real coverage gap** — no way to see dataset schema/structure at all before this; required for `schema_drift` diagnosis |
| `update_dataset_definition` + `rollback_dataset_definition` | `datasets.create_or_update` | `schema_drift` fixes at the dataset level (not just pipeline level) — same snapshot/rollback pattern as `update_pipeline_definition` |
| `get_data_flow_definition` | `data_flows.get` | **Real coverage gap** — pipeline definition tools only show that an activity references a Data Flow by name, never the transformation graph itself. A `schema_drift`/`business_logic` failure inside a Mapping Data Flow was previously invisible |
| `update_data_flow_definition` + `rollback_data_flow_definition` | `data_flows.create_or_update` | Fixes inside the data flow transformation graph, same snapshot/rollback pattern |

**Also fixed while auditing:** `rerun_pipeline` was the one mutating tool without a required `reason` field, breaking the §5 pattern (every mutating tool's `reason` must surface in Claude Desktop's native approval dialog). Added `reason: str` as a required param/schema field; it's now included in the tool's return value too.

**Snapshot mechanism generalized:** `_snapshot_path(factory_name, pipeline_name)` → `_snapshot_path(kind, factory_name, resource_name)`, so pipeline/dataset/data-flow snapshots don't collide in `mcp_adf/_snapshots/` (files are now `pipeline__<factory>__<name>.json`, `dataset__...`, `dataflow__...`).

**Verified:** all 29 tool schemas match `TOOL_REGISTRY` exactly, every mutating tool schema requires `reason`, both files import cleanly. Live-tested the three new read-only sweep tools (`list_triggers`, `list_linked_services`, `list_datasets`) against the real `adf-mcp-test` factory — `list_linked_services` correctly returned the one configured linked service (`PostgreSql1`), the other two correctly returned empty (factory has no triggers/datasets configured).

**Not yet done, unchanged:** no end-to-end Wait-activity insertion test in Claude Desktop; reconciliation with `src/mcp_servers/adf/` remains parked; the new dataset/data-flow definition-edit tools are untested against a real schema-drift or data-flow scenario (same caveat that applied to `update_pipeline_definition` before its first live test).

---

## 11. SDK upgrade evaluated and rejected (2026-07-08)

Tried upgrading `azure-mgmt-datafactory` 6.1.0 → 10.0.0 (latest stable on PyPI) in the real `.venv` per explicit request, with intent to keep it if it worked cleanly.

**Result: reverted.** 10.0.0 is not a compatible version bump — the SDK's codegen switched from the old `msrest`-based `Model` (`_attribute_map`, keyword constructors, `.deserialize()`/`.as_dict()`) to a new dict-based `_utils.model_base.Model`. Concretely, live-tested against the real factory and confirmed:
- `RunQueryFilter`/`RunFilterParameters` no longer accept the `operand=`/`operator=`/`values=` constructor kwargs this codebase uses — breaks `get_activity_run_error`, `get_pipeline_run_history`, `get_activity_run_history`, `get_trigger_run_history`.
- `.deserialize()` is removed entirely from `PipelineResource`/`DatasetResource`/`DataFlowResource` — breaks all 6 mutating definition-edit tools (`update_pipeline_definition`, `rollback_pipeline_definition`, and the dataset/data-flow equivalents), which use it to turn an edited dict back into a resource object before `create_or_update`.

That's 10 of 29 tools broken by the upgrade, for **zero functional gain** — §10's SDK audit already confirmed every operation group this project uses has identical methods across 6.1.0–10.0.0; the only genuinely new group (`IntegrationRuntimeOperations`, Data Flow debug-session interactive query) is infra-level and out of scope anyway.

**Decision: stay pinned to `azure-mgmt-datafactory==6.1.0`.** Reverted the `.venv` and re-verified `list_pipelines` and `get_pipeline_run_history` both still work live against the real factory. If a future need forces migration (e.g. Azure deprecates 6.1.0's API version), expect a real rewrite of model construction across most of `tools.py`, not a version-string bump.

**Claude Desktop is now configured:** `nexus-adf` MCP server registered in `claude_desktop_config.json` (packaged-app path: `%LOCALAPPDATA%\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude\claude_desktop_config.json` — this machine's Claude Desktop is a Store/MSIX install, not the unpackaged `%APPDATA%\Claude` path), pointing at this folder's `.venv` python and `mcp_adf/server.py`, with `PYTHONPATH` set to the project root (needed because `server.py` does `from mcp_adf import tools`, which requires the project root on `sys.path` — not satisfied by cwd when Claude Desktop launches the server directly). Live end-to-end testing through Claude Desktop's native approval dialog is the user's to run — not something drivable from this coding session.

---

## 12. Moved `_snapshots/` to project root (2026-07-08)

§8 and §10 above describe `_snapshots/` living inside `mcp_adf/` (`mcp_adf/_snapshots/`) — that was accurate at the time but inconsistent with `logs/`, which was always placed at the project root (`audit.py`'s `_LOG_DIR` uses `Path(__file__).parents[1]`). Per request, made both runtime-created folders consistent: `_snapshot_path()` in `tools.py` now also uses `Path(__file__).parents[1] / "_snapshots"`, so `_snapshots/` lands next to `logs/` and `mcp_adf/` at the project root, not nested inside the package.

Updated `.gitignore` (`mcp_adf/_snapshots/` → `_snapshots/`) and `README.md`'s structure diagram to match. No existing snapshot files needed migrating — none had been created yet (runtime-created, and no mutating definition-edit tool has been exercised live so far).

---

## 13. Milestone: Wait-activity end-to-end test actually succeeded in Claude Desktop (discovered 2026-07-08)

**§8/§9/§10/§11/§12 above all repeat "no end-to-end Wait-activity insertion test in Claude Desktop" as a known gap. That statement was wrong as of this section — it already happened.**

While making a further path change, found real evidence in a stray `project/logs/2026-07-08/audit.jsonl` (the user had manually organized `logs/` and `_snapshots/` into a `project/` subfolder via drag-and-drop, which is what led to this discovery). The log contains a genuine live session, not test data written by this coding session:

1. `list_pipelines`, `list_triggers`, `list_linked_services`, `get_linked_service`, `get_pipeline_definition_raw` — diagnosis reads against the real `adf-mcp-test` factory.
2. `update_pipeline_definition` on `pl_mcp_test2` — **the actual Wait-activity insertion case §7 was designed around**: inserted a `Wait1` (5s) activity, rewired `Fail1`'s `dependsOn` to point at `Wait1`, with `reason: "User requested a wait activity be added before the intentional Fail activity in pl_mcp_test2"` and a proper `change_summary`. Succeeded — meaning this went through Claude Desktop's native approval dialog as designed in §5, and the human approved it.
3. `rollback_pipeline_definition` on the same pipeline — reverted cleanly, snapshot consumed as designed.

**This closes the last major open item from §7/§8's build order.** The full propose → native-approve → apply → verify(-able) → rollback loop is now proven live, not just unit-tested by this coding session. What's *not* yet proven: an end-to-end case where the fix was needed to unblock a real failing run (this session's test used `pl_mcp_test2`'s always-failing `Fail1` activity as a stand-in target, not a genuine failure diagnosed from a live incident) — the diagnose→decide-if-solvable reasoning itself hasn't been tested under a real failure yet, only the mutation+approval+rollback mechanics.

**Also this session:** moved `project/_snapshots/` and `project/logs/` (superseding §12's plain `_snapshots/`/`logs/` at the bare project root) — `mcp_adf/audit.py`'s `_LOG_DIR` and `mcp_adf/tools.py`'s `_SNAPSHOT_DIR` now both point at `<project_root>/project/logs` and `<project_root>/project/_snapshots` respectively, matching the folder the user had already created by hand. `.gitignore` simplified to ignore `project/` wholesale. `README.md` updated to match, and its Claude Desktop config example now includes the `PYTHONPATH` env var (previously undocumented, but required — confirmed necessary by the user's own working config). The pre-existing real audit history in `project/logs/2026-07-08/audit.jsonl` was left untouched and new entries confirmed to append correctly alongside it.

---

## 14. `get_pipeline_definition_raw` reframed as a diagnosis tool, not just fix-input (2026-07-08)

User question: activity-level fields like a Copy/Lookup activity's `policy.timeout`, dataset/linked-service references (`inputs`/`outputs`/`linkedServiceName`), and query fields (`sqlReaderQuery`, `queryTimeout`) — can we get those, and would it help diagnosis?

Confirmed with a synthetic `CopyActivity` round-trip through `PipelineResource.deserialize()`/`.as_dict()` that **all of this is already present** in `get_pipeline_definition_raw`'s output — nothing was missing at the SDK/serialization level. The actual gap was framing: `get_pipeline_definition_raw`'s description only pitched it as "the editable structure needed as input to `update_pipeline_definition`" — i.e. call it once a fix is already decided. Meanwhile `get_pipeline_definition` (used during diagnosis, right after `get_activity_run_error`) deliberately strips activities down to `{name, type}` only. Net effect: Claude never saw an activity's actual timeout, query, or dataset/linked-service reference until it was already writing a fix — too late to use that evidence to explain *why* something failed (`timeout`, `config`, `business_logic` categories especially).

**Fix:** reworded both tools' descriptions (in `server.py`'s MCP schemas and the matching docstrings in `tools.py`) — `get_pipeline_definition` now explicitly says "names/types only, use `get_pipeline_definition_raw` for timeout/dataset/query detail," and `get_pipeline_definition_raw` now explicitly says to call it during diagnosis, right after `get_activity_run_error` identifies the failing activity, not only when preparing a fix. No behavior change — verified live, same output as before.

---

## 15. `create_pipeline` tool added (2026-07-08)

Real trigger: a colleague asked (via a message pasted into the diagnosis chat) to create `pl_creating_new_pipeline` — a one-activity WebActivity pipeline calling `https://api.ipify.org?format=json` — because they lacked ADF write access themselves. This is the first tool for creating a pipeline that doesn't yet exist (all prior mutating tools either changed run/trigger/IR state or overwrote an *existing* resource's definition).

**Design:**
- `create_pipeline(pipeline_name, definition, reason)` — mutating, requires `reason` like every other mutating tool.
- Explicitly **fails if a pipeline with that name already exists** (`{"error": "pipeline_already_exists", ...}`) rather than silently overwriting it — creation has no snapshot/rollback safety net the way `update_pipeline_definition` does (nothing to roll back *to* on a create), so accidental overwrite would be unrecoverable via this codebase's tools.
- Accepts `definition` in either shape: the flat shape `get_pipeline_definition_raw` uses, or the ARM/Data-Factory-Studio export shape (`{"name": ..., "properties": {"activities": [...], ...}}`, which is what the colleague's pasted JSON actually was) — if a `"properties"` key is present, its contents are used and the wrapper (including its own `"name"`) is discarded. `pipeline_name` is always what determines the actual name created, not the JSON's own `"name"` field.

**Verified live against the real `adf-mcp-test` factory:**
- Confirmed `PipelineResource.deserialize()` correctly parses the ARM-shaped `properties` block (activity name/type/method/url all round-tripped correctly).
- Attempted to create `pl_creating_new_pipeline` with the colleague's exact JSON — **it already existed**, with content matching the request exactly (same activity, method, URL). The collision guard correctly refused to touch it rather than overwrite silently. Left untouched.
- Created and then deleted a throwaway `pl_create_pipeline_tool_test` to confirm the tool actually creates a genuinely new pipeline correctly (used `client.pipelines.delete()` directly for cleanup, since no `delete_pipeline` tool exists yet).

`TOOL_REGISTRY` now has 30 entries. **Not yet built:** `delete_pipeline` — there's currently no way to undo a `create_pipeline` mistake other than manually deleting via Studio or the SDK directly. Not built proactively since it wasn't asked for; worth adding if create_pipeline sees real use and mistakes happen.

---

## 16. Rollback redesigned as a named history stack (2026-07-08)

User request, after asking "only update has snapshot?" (confirmed: yes, only `update_pipeline_definition`/`update_dataset_definition`/`update_data_flow_definition` had snapshot/rollback; `create_pipeline` and every other mutating tool had none): give `create_pipeline` an undo path too, stop using a single overwritten-each-time file per resource (which only ever remembered the *last* change), and support named checkpoints you can query and jump between — not just one-shot consumed undo.

**Storage redesign:** `_snapshot_path()` (one file per resource, overwritten every update, deleted on rollback) replaced with `_snapshot_dir()` (one directory per resource holding its full history — nothing ever deleted): `project/_snapshots/<kind>__<factory>__<resource>/0001__<name>.json`, `0002__<name>.json`, etc. New helpers: `_push_snapshot` (append a checkpoint), `_list_snapshots` (metadata for every checkpoint, oldest first), `_find_snapshot` (by name, newest-match-first so a reused name resolves to its latest occurrence), `_previous_snapshot` (the checkpoint immediately before the current one, for "undo one step" when no name is given), `_ensure_baseline` (captures a resource's as-found content as an `"initial"` checkpoint the first time it's touched, so pre-existing resources like `pl_mcp_test2` — which had update calls before this redesign existed — don't lose their original state the moment the first tracked update happens).

**A real bug found and fixed during this build, not just a design choice:** the first version of this redesign named each checkpoint after the *upcoming* change but stored the *pre-change* content — so `rollback_pipeline_definition(state_name="wait-5s")` actually restored the 1s content that existed *before* the change to 5s, not the 5s content itself. Caught immediately by live-testing the exact "jump to a named state" scenario the user asked for (`wait_time_in_seconds` was still 1, not 5, after the jump). Root-caused and fixed by flipping the model: every checkpoint now stores the resource's content as it exists *at that point* (like a git tag on a commit, not a diff) — `update_*_definition` pushes the checkpoint *after* successfully applying the change, and `create_pipeline` pushes two checkpoints (`before-creation` with `definition=None`, then one for the just-created content, default-named `"created"`). Re-verified live end-to-end after the fix: jump directly to `wait-5s` → confirmed actual content was 5; move forward to `wait-10s` → confirmed 10; undo one step with no `state_name` → correctly landed back on `wait-5s`; full history preserved with repeated visits to the same name all showing up distinctly (by sequence number) in `list_pipeline_snapshots`.

**New tools:** `list_pipeline_snapshots`, `list_dataset_snapshots`, `list_data_flow_snapshots` (read-only — metadata per checkpoint: `state_name`, `sequence`, `action`, `timestamp`, `reason`, `change_summary`). `TOOL_REGISTRY` now has 33 entries.

**Changed tools:** `update_pipeline_definition`/`update_dataset_definition`/`update_data_flow_definition` gained an optional `state_name` param (defaults to a slug of `change_summary`). `rollback_pipeline_definition`/`rollback_dataset_definition`/`rollback_data_flow_definition` gained an optional `state_name` param — omit it to undo just the last change, or pass a name from the matching `list_*_snapshots` tool to jump directly to any point in history, forward or back. `create_pipeline` gained an optional `state_name` param for its created-state checkpoint.

**What "rolling back to a `before-creation`/`action=create` checkpoint" means:** delete the resource (verified live — deleting a test pipeline this way raised a real `ResourceNotFoundError` on the next `get_pipeline_definition_raw` call, confirming actual deletion, not just a local bookkeeping change).

Verified: all 33 tool schemas match `TOOL_REGISTRY`, every mutating tool still requires `reason`, both files import cleanly. All test pipelines and their snapshot directories created during verification were cleaned up from the real factory afterward.

---

## 17. Critical bug: `.as_dict()`/`.deserialize()` wire-format mismatch silently dropped activity fields (2026-07-09)

**User report:** asked Claude Desktop to create a pipeline with a SetVariable activity on completion of a WebActivity, and a Wait activity on completion of the SetVariable — the pipeline was created, but the activities weren't actually connected. A teammate's separate attempt (not through this server — no matching `create_pipeline` call exists anywhere in `project/logs/`) worked correctly.

**Root cause, confirmed with a live isolated test:** `azure-mgmt-datafactory`'s SDK models have two different dict representations — `.as_dict()` (Python attribute names, snake_case: `depends_on`, `wait_time_in_seconds`, `variable_name`, `error_code`) and `.serialize()` (true ADF wire format, camelCase: `dependsOn`, `typeProperties.waitTimeInSeconds`, `typeProperties.variableName`, `typeProperties.errorCode`). `Model.deserialize(some_dict)` only recognizes wire-format keys — feeding it `.as_dict()`'s snake_case output causes every mismatched field to silently vanish into an inert `additional_properties` bucket instead of the real attribute, which stays empty. **This fails with no exception**, and re-reading via the same buggy `get_pipeline_definition_raw` path falsely appeared to confirm the value was preserved, because `.as_dict()` also echoes `additional_properties` back out — this is exactly why this session's own earlier "verified live" claims for the history-stack feature (§16) and the Wait-activity milestone (§13) were both **false positives**: real ADF connections/fields were broken the entire time; only the tool's own self-referential round-trip looked fine.

**Confirmed via the actual audit log** (`project/logs/2026-07-08/audit.jsonl`) that the `dependsOn` values WERE correctly present in what the user's Claude Desktop session sent to `update_pipeline_definition` — so this was never a prompt-authoring mistake. The tool call was correct; `PipelineResource.deserialize()` silently dropped what it was given.

**Fix:** added `_to_wire_dict(resource)` in `tools.py` (`resource.serialize(keep_readonly=True).get("properties", ...)`) and replaced all 12 `.as_dict()` call sites: `get_pipeline_definition_raw`, `get_dataset_definition_raw`, `get_data_flow_definition` (the read tools whose output must be `.deserialize()`-safe), and the snapshot-storage calls inside `create_pipeline`, `update_pipeline_definition`/`rollback_pipeline_definition`, `update_dataset_definition`/`rollback_dataset_definition`, `update_data_flow_definition`/`rollback_data_flow_definition`. Verified against real wire-format ground truth (`pipeline.serialize(keep_readonly=True)`, not the tool's own — previously equally buggy — output) that a fresh test pipeline's `dependsOn` chain and `typeProperties` values now correctly persist.

**Live remediation applied to the two real resources this bug had already damaged:**
- `pl_mcp_test2` — `Fail1.typeProperties.errorCode` had been silently dropped during the earlier Wait-activity-insertion-and-rollback test (§13). Restored to `"302"`.
- `pl_new_pipeline_name_2` — `SetVariable1`/`Wait1` had empty real `dependsOn` (not actually connected, matching exactly what the user reported) and `SetVariable1.typeProperties.variableName` was missing. Restored the full chain `GetPublicIP → SetVariable1 → Wait1` and `variableName: "pipelineReturnValue"`.

Both confirmed correct afterward against real wire-format ground truth, not the tool's own (formerly lossy) read path.

**Follow-up:** dispatched two parallel review agents to sweep `tools.py`, `server.py`, and `audit.py` for any other instance of this bug class, or other genuine defects, before considering this fully closed.

**Agent findings:**
- `tools.py` reviewer: no remaining `.as_dict()` misuse, no snapshot-mechanism inconsistency — every `_push_snapshot`/`_ensure_baseline` call and every `.deserialize()` call site checked. One residual, lower-confidence **design risk, not a code bug**: `create_pipeline`/`update_*_definition` take a caller-supplied `definition` dict with no key-casing validation — a caller who ignores the docstrings and hand-builds a dict with leftover snake_case keys (instead of passing back a `get_*_definition_raw`/`_to_wire_dict`-produced dict) could still silently reproduce the same data loss. Not fixed — flagged as an open question (add defensive validation, or accept the risk since the docstrings already instruct the correct usage).
- `server.py`/`audit.py` reviewer: no instance of the same bug family in `server.py` (it only passes plain dicts through, no SDK model round-trip). No tool-schema/signature drift across all 33 tools. **Confirmed a second, independent real bug**: `audit.log_call()` ran with no fault isolation from the actual tool result — a logging failure (disk error, non-JSON-serializable value) on the success path would raise back to the caller even though the underlying Azure mutation had already succeeded, risking a caller retrying an already-applied irreversible operation; on the error path, a logging failure could mask the original tool exception entirely.

**Second bug fixed:** added `_log_call_best_effort()` wrapping both `audit.log_call()` call sites in `server.py`'s `call_tool()` — a logging failure is now caught and printed to stderr (`[mcp_adf] audit logging failed (tool result unaffected): ...`), never propagated. Verified live by monkeypatching `audit.log_call` to raise `OSError` on both the success path (confirmed `list_pipelines` still returned its correct result) and the error path (confirmed the original `ResourceNotFoundError` was still what the caller saw, not the logging failure) — both correct. Confirmed the two real remediation calls from earlier in this section landed cleanly in `project/logs/2026-07-09/audit.jsonl` with no test pollution from the monkeypatch experiments (since those never reached real disk writes).

---

## 18. Caller-side key-casing validation built; `get_linked_service_definition_raw`/`update_linked_service_definition` added (2026-07-09)

Two follow-ups from §17: build the defensive validation the first review agent flagged as an open residual risk, and a separate, real user-reported gap — `get_linked_service` only ever returned `{name, type}`, with no host/port/connection string visible and no way to fix a misconfigured linked service even if it could be seen.

**Validation added:** `_find_miscased_fields(obj)` recursively walks a just-`.deserialize()`d SDK model object tree and flags any `additional_properties` key that **exactly matches** a real Python attribute name the object type has (e.g. `depends_on`, `wait_time_in_seconds`, `variable_name`, `error_code`) — the unambiguous signature of `.as_dict()`'s output being fed into `.deserialize()`, which only recognizes wire-format (camelCase) keys. `_reject_if_miscased(resource, label)` wraps this into an error dict (`{"error": "possible_miscased_fields", "warnings": [...], "hint": ...}`) returned *before* any mutating API call, so nothing is ever written to ADF when the check fires.

**Two iterations were needed to get this right — both caught by testing against the real bug, not just running it:**
1. First version fuzzy-matched (strip underscores, lowercase) and excluded exact matches — which excluded the *actual* bug pattern itself (`depends_on` normalizes to itself, so it was wrongly treated as "already correct"). Fixed by checking for exact matches instead.
2. That fix then over-triggered on `PipelineResource`'s own top-level attributes (e.g. `activities`, whose wire key is the compound `properties.activities`) — msrest's deserializer correctly flattens these from a bare dict into the real attribute *and* redundantly echoes a raw copy into `additional_properties`, which isn't a bug. Excluding all compound (dotted) wire keys was too broad, though — it also hid the genuine `wait_time_in_seconds` drop, whose wire key (`typeProperties.waitTimeInSeconds`) is *also* compound but does NOT get auto-flattened when nested inside a polymorphic Activity. The correct signal isn't the key's shape at all: check whether the real attribute (`getattr(obj, key)`) actually ended up populated despite the echo — if it did, benign quirk; if it's still empty while the echoed value is non-empty, genuine drop.

**Verified against three cases plus the real historical buggy payload:** correct wire-format input → zero warnings; a genuine custom property (`myCustomTag`) → zero warnings (not flagged as miscased since it doesn't collide with any real attribute name); the original synthetic bug (`depends_on`/`wait_time_in_seconds` on two Wait activities) → all three real drops caught, nothing missed. Re-ran against the *actual* line 15 payload from `project/logs/2026-07-08/audit.jsonl` (the real historical bug) — caught the `dependsOn` drops on both `SetVariable1` and `Wait1` (the connectivity-breaking issue the user reported), though `variableName`/`waitTimeInSeconds` weren't flagged in that specific historical replay because the activities' discriminator (`type`) wasn't cleanly resolved for that payload, so they deserialized as base `Activity` objects lacking those subclass-specific attributes to compare against. Known, accepted limitation — the most severe issue (broken connections) is still reliably caught.

**Known limitation found by deliberately trying to break it:** the validator targets exactly one failure shape — a single field given under its Python attribute name instead of its wire key. A *structurally* wrong payload (e.g. wrapping several fields under a made-up key like `type_properties` instead of the correct `typeProperties`, tested live against `PostgreSql1`) isn't caught locally, because none of the real attribute names appear as top-level `additional_properties` keys — they're nested one level deeper inside the wrong wrapper. In that live test, Azure's own service-side schema validation rejected the malformed payload outright (`HttpResponseError: BadRequest ... typeProperties nested in payload is null`) before anything was written — so this failure mode isn't silent, it's just not caught by our local guard specifically. Not fixed further; the primary, real-world-confirmed bug pattern (single miscased field) is what mattered most and is reliably caught.

**Validation wired into all 9 relevant `.deserialize()` call sites**: `create_pipeline`, `update_pipeline_definition`/`rollback_pipeline_definition`, `update_dataset_definition`/`rollback_dataset_definition`, `update_data_flow_definition`/`rollback_data_flow_definition`, and the two new linked-service tools below.

**New linked-service tools** (`TOOL_REGISTRY` now 37 entries):
- `get_linked_service_definition_raw` — full wire-format definition; `get_linked_service` updated to explicitly say it omits host/port/connection string and point here instead.
- `update_linked_service_definition` / `list_linked_service_snapshots` / `rollback_linked_service_definition` — same named-history-stack pattern as pipeline/dataset/data-flow (§16), including the `action="create"`→delete rollback branch for consistency (though nothing currently pushes a `create` marker for linked services, since there's no `create_linked_service` tool — not built, wasn't asked for).
- One SDK structural quirk handled internally: `LinkedServiceResource.deserialize()` requires the `{"properties": {...}}` wrapper (unlike `PipelineResource`, which accepts the bare dict) — `update_linked_service_definition`/`rollback_linked_service_definition` wrap this internally so callers see the same bare/flat shape as every other resource type's tools.

**Verified live against the real `PostgreSql1` linked service:** `get_linked_service_definition_raw` correctly surfaced the real `server` (`psql-watchtower-dev-uksouth-01.postgres.database.azure.com`), `port` (5432), `database`, `username` — exactly the fields the user reported being unable to see. Ran a full update → list-history → rollback cycle using a harmless `annotations` change (never touched real connection settings): update applied correctly, `port` unchanged throughout, history showed both checkpoints, rollback restored the exact original state. Cleaned up the orphaned snapshot directory left by an earlier now-deleted test pipeline.

**Also this session:** confirmed `get_linked_service_definition_raw` never exposes secrets — the real `PostgreSql1` response has `server`/`port`/`database`/`username` but no `password` field; its `encryptedCredential` is an opaque reference token into ADF's managed credential store (protection mode + a `CredentialId` pointer), not a reversible encoding of the actual secret. Matches documented ADF REST API behavior: secret-typed fields (`SecureString` passwords, service-principal keys, Key Vault references) are write-only — `get`/`list` never return them regardless of storage method. Not independently tested against a linked service using an inline plaintext password specifically (this factory only has the encrypted-credential-store case), but the redaction happens server-side in ADF, not in this codebase, so the mechanism doesn't depend on which secret type is configured.

---

## 19. `get_activity_run_io` added — raw input/output for a specific activity run (2026-07-09)

Real gap identified by the user: none of the existing activity-run tools expose the actual input/output payload ADF captured for one specific execution — `get_activity_run_history` aggregates failure counts, `get_activity_run_error` gives error detail keyed by pipeline_name + event_timestamp, and the definition tools show structure, not runtime data. None show what a Copy/Lookup activity actually ran (post-parameter-substitution) or what it returned.

**Confirmed both feasible and useful before building:** `ActivityRun` (already used internally, just never fully exposed) has real `.input`/`.output` attributes. ADF has no "get activity run by id" API — `ActivityRunsOperations` only exposes `query_by_pipeline_run`, so the new tool queries every activity run for a given pipeline `run_id` within a `days`-sized window (default 30, caller-adjustable) and picks out the one matching `activity_run_id`. Both ids are already surfaced by `get_activity_run_error`'s `leaf`/`execution_path`, so this is a natural follow-up call in the existing diagnosis flow, not a fresh lookup requiring new context.

**Verified live** against the real failed run found earlier in this session (`pl_get_information_schema`, `SocketException: No such host is known` on its `PostgreSqlV2Source` Lookup): correctly returned the actual resolved `input.source.query` (the real SQL that was about to run) and a mostly-empty `output` (`durationInQueue` only — consistent with a run that failed at host resolution before any data transfer started, not a tool bug). `TOOL_REGISTRY` now 38 entries.

---

## 20. `list_activity_runs` added — the activity list `get_activity_run_io` needs an ID for (2026-07-09)

Follow-up to §19: the user described exactly what ADF Studio's monitoring view shows when you drill into a run — every activity in that run, each with its own input/output. `get_activity_run_io` (§19) already covers the "give me full I/O for one activity" half, but only if you already know its `activity_run_id`; nothing listed all activities in a run at once.

**Design decision, deferred to the user and resolved by asking:** full input/output for every activity in one call, or a lightweight list (name/type/status/id) with `get_activity_run_io` as a separate per-activity detail call? User deferred to judgment ("Claude accesses it as needed, not me"). Chose the lightweight list: pipeline runs can have many activities (loops, `ForEach`), and per-activity input/output can be large (e.g. a `Lookup`'s full returned rows) — bundling all of it into one call risks unbounded token cost for a run with many activities. Also matches this codebase's already-established pattern (light list vs. raw/detailed follow-up, same as `get_pipeline_definition`/`get_pipeline_definition_raw`).

`list_activity_runs(run_id, days=30)` — same "no direct API, query by parent run_id + time window" constraint as `get_activity_run_io`, sharing the same `activity_runs.query_by_pipeline_run` call. Returns `activity_run_id`, `activity_name`, `activity_type`, `status`, `start`, `end`, `duration_in_ms` per activity — no `input`/`output` (that's what `get_activity_run_io` is for, once you have the `activity_run_id` from this list).

**Verified live** against the same real `pl_get_information_schema` failed run — correctly listed its one `GetInformationSchema` Lookup activity with matching timing/status, `activity_run_id` matching what `get_activity_run_io` already independently confirmed works. `TOOL_REGISTRY` now 39 entries.

---

## 21. Native-approval "always allow" — a client setting, not a server one; added server-level `instructions` for the diagnose→propose→execute→verify workflow (2026-07-09)

Two asks: (1) can the server force every mutating-tool approval dialog to offer only "allow once"/"deny", never a persistent "always allow"; (2) can Claude be made to investigate thoroughly and propose a numbered remediation plan before acting, rather than jumping straight to a fix.

**(1) is not controllable from this codebase.** MCP's `ToolAnnotations` (`readOnlyHint`/`destructiveHint`/`idempotentHint`/`openWorldHint` — already set correctly on every mutating tool) only *hint* at risk to shape how Claude Desktop renders its dialog; the spec has no field for "never let the client cache this approval." Whether "Always Allow" is offered — and whether that can be disabled — is entirely a Claude Desktop client-side setting, outside what this MCP server (or this coding session, which has no GUI access to Claude Desktop) can configure. Point the user at Claude Desktop's own Settings (likely a permissions/tool-approval section) to check for this control directly.

**(2) is genuinely buildable, via a real MCP mechanism most servers don't use.** `mcp.server.Server` accepts an `instructions: str | None` constructor param, confirmed (by reading `Server.create_initialization_options()`'s source) to flow directly into `InitializationOptions.instructions` — sent once during the MCP `initialize` handshake, which clients can surface to the model as server-scoped guidance, automatically, every session, regardless of what the user types. This is a better fit than cramming workflow guidance into individual tool descriptions (already partially done for `get_pipeline_definition_raw` etc. — this doesn't replace that, it adds a higher-level workflow layer).

Added a 5-step `_INSTRUCTIONS` string to `Server("nexus-adf", instructions=_INSTRUCTIONS)`: (1) diagnose fully using the read tools before proposing anything — don't guess from the error message alone if a raw-definition or activity-IO tool would show the real cause; (2) propose a numbered plan (root cause, fix, tool(s), verification method) before calling any mutating tool, and explicitly stop instead of proposing a fix for `credential_expired`/`permissions`/platform-outage cases (human-only, permanently, per §4/§5's original design); (3) execute one mutating step at a time, with `reason` written to state the specific diagnosis (not a generic phrase) since it's the only context the human sees at the approval moment; (4) verify after applying a fix rather than declaring success on a successful write; (5) use the matching `rollback_*_definition` tool if verification fails, rather than leaving a bad change in place.

Verified live: `server.create_initialization_options().instructions` correctly returns the full text, confirming it reaches the point in the protocol handshake where Claude Desktop would receive it.

---

## 22. Real `back`/`forward` navigation (fixing a flip-flop bug), content-addressed snapshot storage, kind-first folder layout, and global-parameter tooling (2026-07-09)

**Bug found in the existing rollback mechanism, not a new feature request initially.** `rollback_*_definition(state_name=None)` — the "undo last change" shortcut from §16 — used `_previous_snapshot()`, which read "the file physically before the last one" in the resource's history folder. Since every rollback *appended* a new copy of the target content, calling it twice in a row didn't continue walking backward through history — it flip-flopped between the same two states forever (`[v1,v2,v3]` → back → `v2` (append v4) → back again → reads "the file before v4," which is now `v3`, the one just left → flip-flops v2⇄v3, never reaching `v1`). Found by reasoning through the exact repeated-call sequence, not by a live test — **not yet exercised against the real factory this session**, only verified via local simulation (see below).

**Fix: separated "history" from "where you currently are," like `git checkout`.** History (`_list_snapshots`) is now a genuinely immutable, append-only log — nothing is ever added to it except real create/update calls. A new per-resource `_cursor.json` tracks which checkpoint the live resource *currently* matches, independent of the log. `_step_snapshot(kind, factory, resource, direction)` moves the cursor ±1 through the existing log without writing anything new to it. New tools `back_*_definition`/`forward_*_definition` (mirroring `git checkout HEAD~1` / `HEAD@{1}`) added for all four kinds that already had rollback (pipeline, linked service, dataset, data flow) — 8 new tools. `rollback_*_definition` is kept for jumping to an arbitrary named checkpoint (still copy-and-append, which is correct for that specific use case — a deliberate jump, not a repeated step), but its `state_name` param is now **required** (the buggy no-name shortcut was removed rather than left as a silent trap).

**Verified via local simulation only:** pushed synthetic `v1`/`v2`/`v3` checkpoints into an isolated temp directory (monkeypatched `_SNAPSHOT_DIR`), confirmed `back`→`back`→`back` correctly walks `v3→v2→v1` (not the old flip-flop), `forward`→`forward` correctly returns `v1→v2→v3`, both directions correctly error at their respective ends (`no_earlier_state_available`/`no_later_state_available`), and confirmed the log itself stayed at exactly 3 entries throughout (nothing appended by navigation). **Not yet called live against the real `adf-mcp-test` factory** — the actual Azure `create_or_update`/`delete` calls inside `back_*`/`forward_*` are unverified in this session; only the cursor-arithmetic logic that decides *what* to apply has been tested.

**Second fix, requested by the user, not found independently:** any navigation (`rollback_*`, `back_*`, `forward_*`) landing on a checkpoint predating the resource's creation used to delete the live resource unconditionally. Added a `confirm_delete: bool = False` param to all of these — landing on a pre-creation checkpoint now returns `{"requires_confirmation": true, "would": "delete", "message": ...}` instead of deleting, and only proceeds once re-called with `confirm_delete=true`. This is a code-level gate, separate from (and in addition to) Claude Desktop's own native approval dialog for the mutating tool call itself.

**Storage format redesigned to be content-addressed, like git**, per explicit user request comparing it to git internals: each unique definition is hashed (`sha256(canonical_json)[:16]`) and stored once in `_blobs/<hash>.json`; an append-only `_index.jsonl` records the ordered timeline (`sequence`, `state_name`, `reason`, `timestamp`, `hash`) pointing into that blob store. Re-visiting identical content (e.g. `A→B→A`) now reuses the existing blob instead of duplicating it — confirmed on the real historical data during migration (see below), not just synthetic tests: `PostgreSql1`'s two `"initial"` checkpoints (§18) collapsed onto one shared blob. Replaces the prior one-file-per-checkpoint design where the filename was a slug of `state_name`/`reason` (redundant, since `state_name` is already a field inside the JSON).

**Folder layout changed** from `project/_snapshots/<kind>__<factory>__<resource>/` (flat, kind+factory+resource all in one directory name) to `project/_snapshot/<kind>/<factory>__<resource>/` (nested by kind first, so all pipelines/linked-services/datasets/data-flows group together across factories) — user's explicit choice over a factory-first alternative. **All existing real snapshot data was migrated in place**, not discarded: the three real resource histories from earlier sessions (`PostgreSql1`, `pl_mcp_test2`, `pl_new_pipeline_name_2`) were converted from the old numbered-file format into the new blob+index format and moved to the new path, verified afterward to still read correctly via `_list_snapshots`/`_find_snapshot`/`_step_snapshot`. One stray non-snapshot file found alongside the old data (`pipeline__adf-mcp-test__pl_new_pipeline_name_2.json`, unclear origin, not matching the snapshot-file naming pattern) was preserved by moving it rather than deleting it, since its purpose wasn't clear.

**`project/logs/` renamed to `project/_logs/`** (both the physical folder and `audit.py`'s `_LOG_DIR` constant) for naming consistency with `_snapshot`'s underscore-prefix convention — existing log files moved, not recreated.

**`_INSTRUCTIONS` (§21) updated**: step 5 now names `back_*_definition` as the primary "undo last change" tool (matching its no-`state_name`-needed design), with `rollback_*_definition` reframed as the "jump to a specific earlier named checkpoint" tool, and `forward_*_definition` mentioned for re-applying a change after stepping back from it.

**Global parameter tooling added** — the fifth ADF resource kind to get the full snapshot/rollback/back/forward treatment (`list_global_parameters`, `get_global_parameter_definition_raw`, `update_global_parameter_definition`, `list_global_parameter_snapshots`, `rollback_global_parameter_definition`, `back_global_parameter_definition`, `forward_global_parameter_definition` — 7 new tools). **Explicitly NOT verified against a live factory** — the SDK's `GlobalParameterResource.properties` is typed as `dict[str, GlobalParameterSpecification]` (keyed by parameter name) despite the CRUD API operating per-name, an odd shape inferred purely from `_attribute_map`/constructor-signature introspection, never confirmed against a real `list_global_parameters`/`get` response. Flagged directly in the tool's own docstring as suspect until checked against `adf-mcp-test` (which may have zero global parameters configured, same as it had zero triggers/datasets at earlier points in this doc — worth checking `list_global_parameters` first before trusting `update_global_parameter_definition`).

**Health-check sweep (4 parallel agents) run after the above changes**, specifically to check for other instances of the same "stale index after mutation" bug class and confirm nothing else drifted:
- Audit/approval wiring: confirmed `mutating` for `audit.log_call` is derived purely from each tool's `ToolAnnotations` (no hardcoded tool-name list anywhere to have missed) — all 8 new `back_*`/`forward_*` tools correctly annotated.
- Schema-vs-implementation consistency: all 12 rollback/back/forward functions (4 kinds × 3) verified to have matching params between `tools.py` and `server.py`, correct per-kind SDK client calls, and complete `TOOL_REGISTRY`/`_TOOLS` registration — no copy-paste drift found across the four kinds.
- Similar-bug sweep: no other "read the last/previous item from a mutable collection" pattern found anywhere else in `mcp_adf/`; confirmed zero remaining references to the deleted `_previous_snapshot`.
- **Unrelated but significant finding: diagnosed why several edits made earlier in this same session had silently reverted on disk mid-session** (an Edit-tool call would report success, a later re-read showed the pre-edit content, as if nothing had happened). Root cause: this machine's global VS Code setting `files.autoSave: "afterDelay"`, combined with `server.py`/`tools.py` being open in VS Code with stale in-memory buffers (confirmed via VS Code's Local History snapshots and `CreationTime == LastWriteTime`-to-the-second on both files, the signature of a full-buffer overwrite) — VS Code's autosave timer periodically flushed its stale buffer back over this session's direct-to-disk edits, with no conflict prompt. Not a bug in this codebase or in the coding session's tooling. **Recommendation given to the user: set `files.autoSave` to `"off"` or `"onFocusChange"`, or close/revert any file in VS Code before an external tool edits it directly.** Not fixed by this session (it's an editor setting, not project code) — the user was asked whether to change it and hadn't confirmed as of this writing.

**Tool count now 54** (up from 39 at §20/§21). All 54 schemas verified to match `TOOL_REGISTRY` exactly and both files import cleanly — but note the verification standard from §17: schema/registry consistency checks and local simulation are not a substitute for a live test against the real factory. **Not yet done:** no live call of any `back_*`/`forward_*`/global-parameter tool against `adf-mcp-test`; the content-addressed storage redesign has only been exercised against migrated historical data, never a fresh live write; reconciliation with `src/mcp_servers/adf/` remains parked, unchanged since §1.
