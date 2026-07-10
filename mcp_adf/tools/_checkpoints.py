import hashlib
import json
import re
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

# Rollback snapshots: content-addressed, like git. Each resource's definition content is
# stored once per unique hash in "_blobs/<hash>.json"; an append-only "_index.jsonl" records
# the ordered timeline of checkpoints (timestamp/reason/state_name/hash), so re-visiting
# identical content (e.g. A -> B -> A) reuses the existing blob instead of duplicating it.
# A per-resource "_cursor.json" tracks which checkpoint the live Azure resource currently
# matches, independent of the index, so back_*/forward_* can move through history like
# `git checkout` without ever rewriting the index. project/_snapshot/, sibling to mcp_adf/
# under the project root (same placement convention as project/_logs/ in audit.py).
_SNAPSHOT_DIR = Path(__file__).parents[2] / "project" / "_snapshot"


def _snapshot_dir(kind: str, factory_name: str, resource_name: str) -> Path:
    """One directory per resource, grouped by kind first (all pipelines together, etc)."""
    d = _SNAPSHOT_DIR / kind / f"{factory_name}__{resource_name}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _blob_dir(kind: str, factory_name: str, resource_name: str) -> Path:
    d = _snapshot_dir(kind, factory_name, resource_name) / "_blobs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _hash_definition(definition: dict | None) -> str | None:
    """
    Short content hash, like a git blob id — identical content always hashes the same, so
    re-visiting an earlier definition (e.g. A -> B -> A) reuses the existing blob instead
    of storing a duplicate copy. None (the action="create" case: resource didn't exist) has
    no content to hash.
    """
    if definition is None:
        return None
    canonical = json.dumps(definition, sort_keys=True).encode()
    return hashlib.sha256(canonical).hexdigest()[:16]


def _write_blob(kind: str, factory_name: str, resource_name: str, definition: dict | None) -> str | None:
    digest = _hash_definition(definition)
    if digest is None:
        return None
    path = _blob_dir(kind, factory_name, resource_name) / f"{digest}.json"
    if not path.exists():
        path.write_text(json.dumps(definition))
    return digest


def _read_blob(kind: str, factory_name: str, resource_name: str, digest: str | None) -> dict | None:
    if digest is None:
        return None
    return json.loads((_blob_dir(kind, factory_name, resource_name) / f"{digest}.json").read_text())


def _index_path(kind: str, factory_name: str, resource_name: str) -> Path:
    return _snapshot_dir(kind, factory_name, resource_name) / "_index.jsonl"


def _cursor_path(kind: str, factory_name: str, resource_name: str) -> Path:
    return _snapshot_dir(kind, factory_name, resource_name) / "_cursor.json"


def _read_cursor(kind: str, factory_name: str, resource_name: str, entries: list[dict]) -> int:
    """
    Sequence number the live Azure resource currently matches. Defaults to the newest
    entry if no cursor has been written yet (resources with history predating this file).
    """
    p = _cursor_path(kind, factory_name, resource_name)
    if p.exists():
        return json.loads(p.read_text())["sequence"]
    return entries[-1]["sequence"]


def _write_cursor(kind: str, factory_name: str, resource_name: str, sequence: int) -> None:
    _cursor_path(kind, factory_name, resource_name).write_text(json.dumps({"sequence": sequence}))


def _slugify(text: str, max_len: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:max_len].strip("-") or "state"


def _push_snapshot(
    kind: str, factory_name: str, resource_name: str, *,
    definition: dict | None, reason: str, change_summary: str | None = None,
    state_name: str | None = None, action: str = "exists",
) -> dict:
    """
    Appends one line to this resource's index — a timestamped pointer at the content's
    hash, like a git commit pointing at a tree. Identical content (e.g. re-visiting an
    earlier definition) reuses the existing blob instead of storing it again. Nothing in
    the index is ever rewritten or removed, so every checkpoint stays queryable
    (list_*_snapshots) and re-visitable (rollback/back/forward) indefinitely.
    `action="create"` is the one special case: it means "the resource did not exist at
    this point" (definition=None, no blob) — moving to it means delete, not restore.
    """
    index_path = _index_path(kind, factory_name, resource_name)
    sequence = (sum(1 for _ in index_path.open("r", encoding="utf-8")) if index_path.exists() else 0) + 1
    digest = _write_blob(kind, factory_name, resource_name, definition)
    entry = {
        "sequence": sequence,
        "state_name": state_name or _slugify(change_summary or reason),
        "action": action,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "reason": reason,
        "change_summary": change_summary,
        "hash": digest,
    }
    with index_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    # A genuine create/update always becomes the new "current" state, regardless of
    # where the cursor was left by a previous back_*/forward_* call.
    _write_cursor(kind, factory_name, resource_name, sequence)
    return {**entry, "definition": definition}


def _ensure_baseline(kind: str, factory_name: str, resource_name: str, current_definition: dict, reason: str) -> None:
    """
    If this resource has no history yet (e.g. it already existed before any create_pipeline/
    update_*_definition call ever touched it), captures its as-found content as the first
    checkpoint ("initial") before any change is applied — otherwise that original state
    would never be nameable or reachable again once the first update overwrites it.
    """
    if not _list_snapshots(kind, factory_name, resource_name):
        _push_snapshot(
            kind, factory_name, resource_name, definition=current_definition, reason=reason,
            change_summary="captured as first-seen state", state_name="initial",
        )


def _list_snapshots(kind: str, factory_name: str, resource_name: str) -> list[dict]:
    """Metadata only (no `definition` blob — that's a separate _read_blob call) for every
    checkpoint, oldest first."""
    index_path = _index_path(kind, factory_name, resource_name)
    if not index_path.exists():
        return []
    with index_path.open("r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _find_snapshot(kind: str, factory_name: str, resource_name: str, state_name: str) -> dict | None:
    """Newest-first search, so a re-used name resolves to its most recent occurrence."""
    for entry in reversed(_list_snapshots(kind, factory_name, resource_name)):
        if entry["state_name"] == state_name:
            return {**entry, "definition": _read_blob(kind, factory_name, resource_name, entry["hash"])}
    return None


def _step_snapshot(kind: str, factory_name: str, resource_name: str, direction: str) -> dict:
    """
    Moves the resource's cursor one step through its existing history — like
    `git checkout HEAD~1` / `git checkout HEAD@{1}` — without appending anything to the
    index. `direction` is "back" or "forward". Returns either {"error": ...} or the full
    snapshot entry (incl. `definition`) the caller should now apply to the live resource.
    Callers must call _write_cursor(..., entry["sequence"]) themselves once they've
    actually applied the change (so a no-op preview never moves the cursor).
    """
    entries = _list_snapshots(kind, factory_name, resource_name)
    if not entries:
        return {"error": "no_history"}
    seqs = [e["sequence"] for e in entries]
    cursor = _read_cursor(kind, factory_name, resource_name, entries)
    idx = seqs.index(cursor) if cursor in seqs else len(seqs) - 1
    new_idx = idx - 1 if direction == "back" else idx + 1
    if new_idx < 0:
        return {"error": "no_earlier_state_available"}
    if new_idx >= len(entries):
        return {"error": "no_later_state_available"}
    target = entries[new_idx]
    return {**target, "definition": _read_blob(kind, factory_name, resource_name, target["hash"])}


def _navigate(
    kind: str, factory_name: str, resource_name: str, name_key: str,
    direction: str, reason: str, confirm_delete: bool,
    delete_fn: Callable[[], None], apply_fn: Callable[[dict], dict | None],
) -> dict:
    """
    Shared back_*/forward_* engine for all four resource kinds. `delete_fn` deletes the
    live resource (used when the target checkpoint predates its creation); `apply_fn`
    pushes the target's stored definition to the live resource and returns an error dict
    on failure, None on success. Callers supply these as closures so this stays kind-
    agnostic; see _step_snapshot for how the cursor moves without touching the index.
    """
    target = _step_snapshot(kind, factory_name, resource_name, direction)
    if "error" in target:
        return {**target, name_key: resource_name}

    if target["action"] == "create":
        if not confirm_delete:
            return {
                "requires_confirmation": True,
                "would": "delete",
                name_key: resource_name,
                "target_state": target["state_name"],
                "message": (
                    f"Stepping {direction} to '{target['state_name']}' means this {kind} did not "
                    "exist yet at that point — proceeding will DELETE the live resource in Azure. "
                    "Ask the human before proceeding; call again with confirm_delete=true only "
                    "after they agree."
                ),
            }
        delete_fn()
        _write_cursor(kind, factory_name, resource_name, target["sequence"])
        return {name_key: resource_name, "moved_to": target["state_name"], "deleted": True, "reason": reason}

    error = apply_fn(target["definition"])
    if error:
        return error
    _write_cursor(kind, factory_name, resource_name, target["sequence"])
    return {name_key: resource_name, "moved_to": target["state_name"], "reason": reason}


