from datetime import timedelta, timezone

from azure.mgmt.datafactory import DataFactoryManagementClient

from mcp_adf.auth import get_credential

_IST = timezone(timedelta(hours=5, minutes=30))


def _client(tenant_id: str, client_id: str, client_secret: str, subscription_id: str) -> DataFactoryManagementClient:
    return DataFactoryManagementClient(get_credential(tenant_id, client_id, client_secret), subscription_id)


def _to_ist(value) -> str | None:
    """
    ADF Studio's Monitor UI silently renders timestamps in the browser's local timezone
    (IST for this team) with no timezone label, while the SDK returns UTC — the same run
    can look "off by 5:30" when eyeballed next to Studio unless this is converted too.
    """
    if value is None:
        return None
    return value.astimezone(_IST).strftime("%Y-%m-%d %H:%M:%S IST")


def _to_wire_dict(resource) -> dict:
    """
    True wire-format dict (camelCase, e.g. "dependsOn", "typeProperties.waitTimeInSeconds")
    — the shape PipelineResource/DatasetResource/DataFlowResource.deserialize() actually
    expects, and the same shape ADF Studio's "Code" view / ARM export uses.

    NOT the same as `.as_dict()`, which uses Python attribute names (snake_case) and is
    UNSAFE to feed back into `.deserialize()`: any field whose wire name differs from its
    Python attribute name (dependsOn/depends_on, waitTimeInSeconds/wait_time_in_seconds,
    variableName/variable_name, errorCode/error_code, ...) silently vanishes into an inert
    `additional_properties` bucket instead of the real attribute, which then serializes
    to ADF as empty/missing — activities appear created but aren't actually connected,
    and typeProperties like variable names or error codes are silently dropped.
    """
    serialized = resource.serialize(keep_readonly=True)
    return serialized.get("properties", serialized)


def _find_miscased_fields(obj, path: str = "") -> list[str]:
    """
    Recursively walks a deserialized SDK model object tree looking for
    `additional_properties` keys that are a miscased (snake_case) version of a REAL
    attribute the model has under a different, correctly-cased name — the exact failure
    mode that caused the dependsOn/typeProperties bug (see _to_wire_dict): a caller-supplied
    key `Model.deserialize()` doesn't recognize is silently dropped into
    `additional_properties` instead of raising, so the field never reaches ADF, with no
    error to signal it. Returns human-readable warnings; empty list if nothing looks wrong.

    Deliberately does NOT flag every `additional_properties` entry — ADF genuinely allows
    arbitrary custom properties on activities. Only flags a key that EXACTLY matches a real
    Python attribute name this object type has (e.g. "depends_on", "wait_time_in_seconds").
    That's the unambiguous signature of the bug: `.as_dict()`'s output (Python attribute
    names) fed into `.deserialize()`, which only recognizes wire keys (e.g. "dependsOn") —
    a genuine custom property would essentially never happen to collide with a real
    attribute's exact Python name.
    """
    warnings: list[str] = []
    if obj is None:
        return warnings
    if isinstance(obj, list):
        for i, item in enumerate(obj):
            warnings.extend(_find_miscased_fields(item, f"{path}[{i}]"))
        return warnings
    if isinstance(obj, dict):
        for k, v in obj.items():
            warnings.extend(_find_miscased_fields(v, f"{path}.{k}" if path else k))
        return warnings

    attribute_map = getattr(obj, "_attribute_map", None)
    if attribute_map is None:
        return warnings

    extra = getattr(obj, "additional_properties", None) or {}
    real_names = set(attribute_map.keys()) - {"additional_properties"}
    for key, extra_value in extra.items():
        if key not in real_names or not extra_value:
            continue
        # Some compound wire keys (e.g. "properties.activities" on PipelineResource
        # deserialized from a bare/unwrapped dict) get correctly flattened into the real
        # attribute by msrest's deserializer AND redundantly echoed into
        # additional_properties — a benign quirk, not dropped data. Distinguish that case
        # from a genuine drop by checking whether the real attribute actually ended up
        # populated: if it did, this is the harmless echo; if it's still empty/None while
        # additional_properties holds a real value, the field was genuinely never applied.
        if getattr(obj, key, None):
            continue
        correct_wire_key = attribute_map[key]["key"]
        warnings.append(
            f'{path or "root"}: key "{key}" was silently ignored — it looks like a '
            f'miscased version of the real field (wire key "{correct_wire_key}"; ADF '
            f'uses camelCase, not snake_case). This value will NOT be applied.'
        )

    for attr_name in real_names:
        if attr_name == "additional_properties":
            continue
        try:
            value = getattr(obj, attr_name)
        except AttributeError:
            continue
        warnings.extend(_find_miscased_fields(value, f"{path}.{attr_name}" if path else attr_name))
    return warnings


def _reject_if_miscased(resource, resource_label: str) -> dict | None:
    """
    Returns an error dict (never write anything to ADF) if the just-deserialized resource
    has any miscased-field warnings, else None. Call this right after every
    `Model.deserialize()` and before the mutating API call that would otherwise silently
    write incomplete data.
    """
    warnings = _find_miscased_fields(resource)
    if not warnings:
        return None
    return {
        "error": "possible_miscased_fields",
        "resource": resource_label,
        "warnings": warnings,
        "hint": 'ADF wire format is camelCase (e.g. "dependsOn", "typeProperties.waitTimeInSeconds"), '
                "not Python-style snake_case. Fix the flagged keys and retry — nothing was written to ADF.",
    }

