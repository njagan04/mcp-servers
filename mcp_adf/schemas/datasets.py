from mcp.types import Tool, ToolAnnotations

TOOLS = [
    Tool(
        name="create_dataset",
        description=(
            "Creates a brand-new dataset. Fails with an explicit error if a dataset with this name "
            "already exists — use update_dataset_definition to modify an existing one instead. Pushes "
            "a \"did not exist\" marker onto this dataset's history stack, so rollback_dataset_definition "
            "can undo the creation (delete it) later. `definition` accepts either the flat shape "
            "get_dataset_definition_raw uses, or the ARM/Data-Factory-Studio export shape "
            "({\"name\": ..., \"properties\": {\"type\": \"...\", ...}}) — if a \"properties\" key is "
            "present, its contents are used and the wrapper is discarded. `dataset_name` (not the "
            "JSON's own \"name\" field, if present) determines the actual name created."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "dataset_name": {"type": "string"},
                "definition": {
                    "type": "object",
                    "description": "Dataset definition JSON — flat shape or {\"name\":..., \"properties\":{...}}.",
                },
                "reason": {
                    "type": "string",
                    "description": "Why this dataset is being created — shown to the user in the approval dialog.",
                },
                "state_name": {
                    "type": "string",
                    "description": "Optional name for the created state. Defaults to \"created\" if omitted.",
                },
            },
            "required": ["dataset_name", "definition", "reason"],
        },
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True),
    ),
    Tool(
        name="list_datasets",
        description="Factory-wide dataset sweep — name, type, and backing linked service for each.",
        inputSchema={
            "type": "object",
            "properties": {
                "factory_name": {"type": "string"},
            },
            "required": ["factory_name"],
        },
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
    ),
    Tool(
        name="get_dataset_definition_raw",
        description=(
            "Full dataset definition (schema, structure, linked service reference, parameters) — the "
            "evidence needed to diagnose schema_drift. Feed the returned dict back into "
            "update_dataset_definition (with edits applied) to apply a fix."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "dataset_name": {"type": "string"},
            },
            "required": ["dataset_name"],
        },
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
    ),
    Tool(
        name="update_dataset_definition",
        description=(
            "Overwrites a dataset's full definition to apply a concrete fix (e.g. correcting a drifted "
            "schema). `definition` must be get_dataset_definition_raw's output with edits applied. "
            "The pre-change definition is pushed onto this dataset's named history stack automatically, "
            "so rollback_dataset_definition can jump back to it later by name (see list_dataset_snapshots)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "dataset_name": {"type": "string"},
                "definition": {"type": "object", "description": "Modified output of get_dataset_definition_raw."},
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
                    "description": "Optional name for the state being saved. Defaults to a slug of "
                                    "change_summary if omitted.",
                },
            },
            "required": ["dataset_name", "definition", "reason", "change_summary"],
        },
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True),
    ),
    Tool(
        name="list_dataset_snapshots",
        description="Lists every named state saved in this dataset's history, oldest first.",
        inputSchema={
            "type": "object",
            "properties": {
                "dataset_name": {"type": "string"},
            },
            "required": ["dataset_name"],
        },
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
    ),
    Tool(
        name="rollback_dataset_definition",
        description=(
            "Jumps directly to a specific named state from list_dataset_snapshots, wherever it sits in "
            "history — nothing is ever deleted. For simple one-step undo/redo without needing a state_name, "
            "use back_dataset_definition / forward_dataset_definition instead. If the target state predates "
            "the dataset's creation, this returns requires_confirmation instead of deleting — ask the human, "
            "then re-call with confirm_delete=true."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "dataset_name": {"type": "string"},
                "reason": {
                    "type": "string",
                    "description": "Why the change is being reverted — shown in the approval dialog.",
                },
                "state_name": {
                    "type": "string",
                    "description": "Name of the state to jump to, from list_dataset_snapshots.",
                },
                "confirm_delete": {
                    "type": "boolean",
                    "description": "Only set true after the human has explicitly agreed to delete the "
                                    "live dataset, in response to a prior requires_confirmation result.",
                },
            },
            "required": ["dataset_name", "reason", "state_name"],
        },
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=True),
    ),
    Tool(
        name="back_dataset_definition",
        description=(
            "Steps one checkpoint back through this dataset's history — see back_pipeline_definition for "
            "the full behavior."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "dataset_name": {"type": "string"},
                "reason": {
                    "type": "string",
                    "description": "Why the change is being reverted — shown in the approval dialog.",
                },
                "confirm_delete": {
                    "type": "boolean",
                    "description": "Only set true after the human has explicitly agreed to delete the "
                                    "live dataset, in response to a prior requires_confirmation result.",
                },
            },
            "required": ["dataset_name", "reason"],
        },
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=True),
    ),
    Tool(
        name="forward_dataset_definition",
        description=(
            "Steps one checkpoint forward through this dataset's history — see forward_pipeline_definition "
            "for the full behavior."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "dataset_name": {"type": "string"},
                "reason": {
                    "type": "string",
                    "description": "Why the change is being applied — shown in the approval dialog.",
                },
                "confirm_delete": {
                    "type": "boolean",
                    "description": "Only set true after the human has explicitly agreed to delete the "
                                    "live dataset, in response to a prior requires_confirmation result.",
                },
            },
            "required": ["dataset_name", "reason"],
        },
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=True),
    ),
]
