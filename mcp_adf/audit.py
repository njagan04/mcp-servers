import json
from datetime import datetime, timezone
from pathlib import Path

# project/_logs/, sibling to mcp_adf/ under the project root.
_LOG_DIR = Path(__file__).parents[1] / "project" / "_logs"


def log_call(name: str, arguments: dict, *, mutating: bool, duration_ms: float,
             result: dict | None = None, error: BaseException | None = None) -> None:
    """Appends one audit entry to logs/<YYYY-MM-DD>/audit.jsonl.

    `arguments` must be the caller-supplied args only (never the merged dict that
    includes `_CREDS`) so credentials never reach disk.
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

    with (day_dir / "audit.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
