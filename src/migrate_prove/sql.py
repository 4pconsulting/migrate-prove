from __future__ import annotations

from migrate_prove.connectors import Connector
from migrate_prove.models import Entity, Mapping


def where_clause(clause: str | None) -> str:
    return f" WHERE {clause}" if clause else ""


def case_lookup(connector: Connector, mapping: Mapping) -> str:
    if not mapping.source or not mapping.lookup:
        if not mapping.source:
            raise ValueError(f"Cannot compile SQL for mapping {mapping.target}")
        return connector.quote(mapping.source)
    parts = [f"CASE {connector.quote(mapping.source)}"]
    for source_value, target_value in mapping.lookup.items():
        parts.append(f"WHEN {literal(source_value)} THEN {literal(target_value)}")
    if mapping.lookup_default is not None:
        parts.append(f"ELSE {literal(mapping.lookup_default)}")
    elif mapping.lookup_on_missing == "null":
        parts.append("ELSE NULL")
    else:
        parts.append(f"ELSE {connector.quote(mapping.source)}")
    parts.append("END")
    return " ".join(parts)


def literal(value: object) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value).replace("'", "''")
    return f"'{text}'"


def mapped_dimension_sql(connector: Connector, entity: Entity, target_col: str) -> str:
    mapping = entity.mapping_for_target(target_col)
    if mapping is None:
        return connector.quote(target_col)
    if mapping.lookup:
        return case_lookup(connector, mapping)
    if mapping.source:
        return connector.quote(mapping.source)
    return connector.quote(target_col)


def group_count_sql(
    connector: Connector,
    table: str,
    schema: str | None,
    dimensions: list[str],
    extra_where: str | None = None,
    dimension_sql: dict[str, str] | None = None,
) -> str:
    qualified = connector.qualified(table, schema)
    selects: list[str] = []
    groups: list[str] = []
    for index, dimension in enumerate(dimensions, start=1):
        expr = (dimension_sql or {}).get(dimension, connector.quote(dimension))
        alias = connector.quote(dimension)
        selects.append(f"{expr} AS {alias}")
        groups.append(str(index))
    select_sql = ", ".join(selects + ["COUNT(*) AS row_count"])
    group_sql = ", ".join(groups)
    return f"SELECT {select_sql} FROM {qualified}{where_clause(extra_where)} GROUP BY {group_sql}"
