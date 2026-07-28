import json
from datetime import datetime, timezone
from pathlib import Path

# project/_logs/, sibling to mcp_adf/ under the project root.
_LOG_DIR = Path(__file__).parents[1] / "project" / "_logs"


def log_call(name: str, arguments: dict, *, mutating: bool, duration_ms: float,
             result: dict | None = None, error: BaseException | None = None,
             blocked: bool = False, requires_confirmation: bool = False) -> None:
    """Appends one audit entry to logs/<YYYY-MM-DD>/audit.jsonl.

    `arguments` must be the caller-supplied args only (never the merged dict that
    includes `_CREDS`) so credentials never reach disk.

    `blocked`/`requires_confirmation` distinguish a tool call that returned an
    {"error": ...} or {"requires_confirmation": ...} dict (no raised exception, but
    also not a genuine success — e.g. a rejected miscased-field write, or the
    pre-delete rollback confirmation gate) from an actual successful mutation. Without
    these, both outcomes were previously indistinguishable from success under "result".
    """
    now = datetime.now(timezone.utc)
    day_dir = _LOG_DIR / now.strftime("%Y-%m-%d")
    day_dir.mkdir(parents=True, exist_ok=True)

    entry = {
        "timestamp": now.isoformat(),
        "tool": name,
        "mutating": mutating,
        "arguments": arguments,
        "duration_ms": round(duration_ms, 1),
    }
    if error is not None:
        entry["error"] = str(error)
    else:
        entry["result"] = result
        if blocked:
            entry["blocked"] = True
        if requires_confirmation:
            entry["requires_confirmation"] = True

    with (day_dir / "audit.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
