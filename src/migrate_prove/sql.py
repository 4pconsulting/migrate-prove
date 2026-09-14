from __future__ import annotations

from migrate_prove.connectors import Connector, RelationRef
from migrate_prove.models import Entity, Mapping


def source_relation(entity: Entity) -> RelationRef:
    if entity.source_sql is not None:
        return RelationRef(sql=entity.source_sql.strip(), name=f"{entity.name}_src")
    return RelationRef(table=entity.source_table, schema=entity.source_schema)


def target_relation(entity: Entity) -> RelationRef:
    return RelationRef(table=entity.target_table, schema=entity.target_schema)


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
