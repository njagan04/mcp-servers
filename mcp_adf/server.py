import asyncio
import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, TextContent, Tool


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
This server exposes Azure Data Factory diagnostic and self-remediation tools.
Full workflow guidance (tool-call economy, diagnose-before-fix, mutation approval,
verification, rollback semantics) is provided in the project instructions — follow it.
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
async def call_tool(name: str, arguments: dict) -> CallToolResult:
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
        _log_call_best_effort(name, arguments, mutating=mutating,duration_ms=(time.monotonic() - start) * 1000, error=exc)
        raise
    # Many tool functions signal a blocked write or a required human confirmation (e.g. the
    # pre-delete rollback gate) by returning an {"error": ...}/{"requires_confirmation": ...}
    # dict rather than raising — without this check those outcomes look identical to a real
    # success at the protocol level (isError=False), which a non-content-sniffing caller
    # (or the audit log) can't tell apart from an actual mutation having happened.
    blocked = isinstance(result, dict) and "error" in result
    requires_confirmation = isinstance(result, dict) and result.get("requires_confirmation") is True
    _log_call_best_effort(
        name, arguments, mutating=mutating, duration_ms=(time.monotonic() - start) * 1000,
        result=result, blocked=blocked, requires_confirmation=requires_confirmation,
    )
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(result))],
        isError=blocked or requires_confirmation,
    )


async def run():
    async with stdio_server() as streams:
        await server.run(*streams, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(run())
