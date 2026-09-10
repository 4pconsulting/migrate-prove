from __future__ import annotations

from typing import Any

from migrate_prove.eval import safe_eval
from migrate_prove.models import Mapping, MappingKind
from migrate_prove.normalize import snake_name


def row_namespace(row: dict[str, Any]) -> dict[str, Any]:
    namespace: dict[str, Any] = dict(row)
    for key, value in row.items():
        namespace[str(key).lower()] = value
        namespace[snake_name(str(key))] = value
    return namespace


def apply_mapping(row: dict[str, Any], mapping: Mapping) -> Any:
    source_value = row.get(mapping.source) if mapping.source else None

    if mapping.kind == MappingKind.default:
        return mapping.default if source_value is None else source_value

    if mapping.lookup:
        if source_value is None:
            key = None
        else:
            key = str(source_value)
        if key in mapping.lookup:
            return mapping.lookup[key]
        if mapping.lookup_default is not None:
            return mapping.lookup_default
        if mapping.lookup_on_missing == "passthrough":
            return source_value
        if mapping.lookup_on_missing == "null":
            return None
        raise ValueError(f"Lookup miss for {mapping.target}: {source_value!r}")

    if mapping.rules:
        namespace = row_namespace(row)
        else_value: Any = None
        for rule in mapping.rules:
            if rule.when:
                if bool(safe_eval(rule.when, namespace)):
                    return rule.then
            elif rule.else_value is not None:
                else_value = rule.else_value
            elif rule.then is not None and rule.when is None:
                else_value = rule.then
        return else_value

    if mapping.expression:
        return safe_eval(mapping.expression, row_namespace(row))

    return source_value
