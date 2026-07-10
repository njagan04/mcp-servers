# Nexus ADF MCP server (Claude Desktop)

Standalone MCP stdio server exposing Azure Data Factory diagnostic + remediation tools to Claude Desktop. Self-contained — has its own `.venv` and `.env`, no dependency on any other folder.

## Setup

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env
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
  }
}
```

Use absolute paths — relative paths are not resolved reliably by Claude Desktop's launcher. `PYTHONPATH` is required so `server.py`'s `from mcp_adf import ...` imports resolve when Claude Desktop launches the script directly (its own cwd isn't this folder).

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
