from mcp.types import Tool, ToolAnnotations

_ALL_KINDS = ["pipeline", "dataset", "linked_service", "data_flow", "trigger", "global_parameter"]

# Consolidation of what used to be N separate top-level MCP tools per operation (one per
# resource kind). Dispatched by resource_type in mcp_adf/tools/_dispatch.py. Each tool's
# annotations match the per-kind tools it replaces exactly (verified identical across
# every kind before consolidating) — collapsing only happens along the resource_type axis,
# never across the mutating/read-only boundary.
#
# Descriptions are deliberately terse — per-kind nuance (ARM export shape only applying to
# pipeline/dataset/data_flow, triggers created Stopped, global_parameter's {"type","value"}
# shape, trigger's Started/Stopped state being separate from its definition) lives in the
# project instructions instead of being repeated inline here, same reasoning as
# server.py's _INSTRUCTIONS trim.
TOOLS = [
    Tool(
        name="get_resource_definition_raw",
        description=(
            "Full raw definition of a resource — evidence for diagnosis, and the editable "
            "structure to feed into update_resource_definition. Not valid for "
            "resource_type=\"trigger\" (use get_trigger instead)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "resource_type": {
                    "type": "string",
                    "enum": ["pipeline", "dataset", "linked_service", "data_flow", "global_parameter"],
                },
                "name": {"type": "string", "description": "Name of the resource to fetch."},
            },
            "required": ["resource_type", "name"],
        },
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
    ),
    Tool(
        name="create_resource",
        description=(
            "Creates a new resource. Fails if one of this resource_type and name already "
            "exists — use update_resource_definition instead. `name` (not any \"name\" field "
            "inside `definition`) determines the name created."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "resource_type": {"type": "string", "enum": _ALL_KINDS},
                "name": {"type": "string", "description": "Name of the resource to create."},
                "definition": {
                    "type": "object",
                    "description": "Resource definition JSON — flat shape or {\"name\":..., \"properties\":{...}}.",
                },
                "reason": {
                    "type": "string",
                    "description": "Why this resource is being created — shown to the user in the approval dialog.",
                },
                "state_name": {
                    "type": "string",
                    "description": "Optional name for the created state. Defaults to \"created\" if omitted.",
                },
            },
            "required": ["resource_type", "name", "definition", "reason"],
        },
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True),
    ),
    Tool(
        name="list_resources",
        description="Factory-wide sweep of every resource of one resource_type — name and light metadata each.",
        inputSchema={
            "type": "object",
            "properties": {
                "resource_type": {"type": "string", "enum": _ALL_KINDS},
            },
            "required": ["resource_type"],
        },
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
    ),
    Tool(
        name="update_resource_definition",
        description=(
            "Overwrites a resource's full definition to apply a fix. ADF has no partial-patch "
            "API — `definition` must be get_resource_definition_raw's output (get_trigger's, "
            "for triggers) with edits applied. Pushes the pre-change definition onto history "
            "(see list_resource_snapshots / rollback_resource_definition)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "resource_type": {"type": "string", "enum": _ALL_KINDS},
                "name": {"type": "string", "description": "Name of the resource to update."},
                "definition": {"type": "object", "description": "Modified output of get_resource_definition_raw."},
                "reason": {
                    "type": "string",
                    "description": "The diagnosed root cause driving this change — shown in the approval dialog.",
                },
                "change_summary": {
                    "type": "string",
                    "description": "One-line human-readable diff of what changed.",
                },
                "state_name": {
                    "type": "string",
                    "description": "Optional name for the state being saved (what the resource looked like "
                                    "before this change). Defaults to a slug of change_summary if omitted.",
                },
            },
            "required": ["resource_type", "name", "definition", "reason", "change_summary"],
        },
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True),
    ),
    Tool(
        name="list_resource_snapshots",
        description=(
            "Lists every named state saved in this resource's history, oldest first — "
            "check before calling rollback_resource_definition with a specific state_name."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "resource_type": {"type": "string", "enum": _ALL_KINDS},
                "name": {"type": "string", "description": "Name of the resource."},
            },
            "required": ["resource_type", "name"],
        },
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
    ),
    Tool(
        name="rollback_resource_definition",
        description=(
            "Jumps to a specific named state from list_resource_snapshots — nothing is ever "
            "deleted. For one-step undo/redo, use back_resource_definition / "
            "forward_resource_definition instead. If the target predates the resource's "
            "creation, returns requires_confirmation before deleting the live resource."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "resource_type": {"type": "string", "enum": _ALL_KINDS},
                "name": {"type": "string", "description": "Name of the resource."},
                "reason": {
                    "type": "string",
                    "description": "Why the change is being reverted — shown in the approval dialog.",
                },
                "state_name": {
                    "type": "string",
                    "description": "Name of the state to jump to, from list_resource_snapshots.",
                },
                "confirm_delete": {
                    "type": "boolean",
                    "description": "Only set true after the human has explicitly agreed to delete the "
                                    "live resource, in response to a prior requires_confirmation result.",
                },
            },
            "required": ["resource_type", "name", "reason", "state_name"],
        },
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=True),
    ),
    Tool(
        name="back_resource_definition",
        description=(
            "Steps one checkpoint back through history, like `git checkout HEAD~1`. Call "
            "forward_resource_definition to step forward again. If the step lands before the "
            "resource existed, returns requires_confirmation before deleting the live resource."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "resource_type": {"type": "string", "enum": _ALL_KINDS},
                "name": {"type": "string", "description": "Name of the resource."},
                "reason": {
                    "type": "string",
                    "description": "Why the change is being reverted — shown in the approval dialog.",
                },
                "confirm_delete": {
                    "type": "boolean",
                    "description": "Only set true after the human has explicitly agreed to delete the "
                                    "live resource, in response to a prior requires_confirmation result.",
                },
            },
            "required": ["resource_type", "name", "reason"],
        },
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=True),
    ),
    Tool(
        name="forward_resource_definition",
        description=(
            "Steps one checkpoint forward through history — the mirror of "
            "back_resource_definition. Returns no_later_state_available at the newest checkpoint."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "resource_type": {"type": "string", "enum": _ALL_KINDS},
                "name": {"type": "string", "description": "Name of the resource."},
                "reason": {
                    "type": "string",
                    "description": "Why the change is being applied — shown in the approval dialog.",
                },
                "confirm_delete": {
                    "type": "boolean",
                    "description": "Only set true after the human has explicitly agreed to delete the "
                                    "live resource, in response to a prior requires_confirmation result.",
                },
            },
            "required": ["resource_type", "name", "reason"],
        },
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=True),
    ),
]
