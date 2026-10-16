from __future__ import annotations

from typing import Any

from migrate_prove.connectors import Connector, RelationRef, TableInfo
from migrate_prove.models import Entity, Mapping, RelationPart


def _part_to_ref(part: RelationPart, name: str) -> RelationRef:
    if part.sql is not None:
        return RelationRef(sql=part.sql.strip(), name=name)
    assert part.table is not None
    return RelationRef(table=part.table, schema=part.schema_name)


def source_relations(entity: Entity) -> list[RelationRef]:
    if entity.source_parts:
        return [
            _part_to_ref(part, f"{entity.name}_src_{index}")
            for index, part in enumerate(entity.source_parts)
        ]
    if entity.source_sql is not None:
        return [RelationRef(sql=entity.source_sql.strip(), name=f"{entity.name}_src")]
    return [RelationRef(table=entity.source_table, schema=entity.source_schema)]


def target_relations(entity: Entity) -> list[RelationRef]:
    if entity.target_parts:
        return [
            _part_to_ref(part, f"{entity.name}_tgt_{index}")
            for index, part in enumerate(entity.target_parts)
        ]
    if entity.target_sql is not None:
        return [RelationRef(sql=entity.target_sql.strip(), name=f"{entity.name}_tgt")]
    return [RelationRef(table=entity.target_table, schema=entity.target_schema)]


def source_relation(entity: Entity) -> RelationRef:
    refs = source_relations(entity)
    if len(refs) != 1:
        raise ValueError(
            f"Entity {entity.name}: expected a single source relation, got {len(refs)} parts"
        )
    return refs[0]


def target_relation(entity: Entity) -> RelationRef:
    refs = target_relations(entity)
    if len(refs) != 1:
        raise ValueError(
            f"Entity {entity.name}: expected a single target relation, got {len(refs)} parts"
        )
    return refs[0]


def fetch_concat(
    connector: Connector, refs: list[RelationRef], where: str | None = None
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for ref in refs:
        rows.extend(connector.fetch_rows(ref, where))
    return rows


def count_concat(
    connector: Connector, refs: list[RelationRef], where: str | None = None
) -> int:
    if len(refs) == 1:
        return connector.count_rows(refs[0], where)
    return sum(connector.count_rows(ref, where) for ref in refs)


def group_count_concat(
    connector: Connector,
    refs: list[RelationRef],
    dimensions: list[str],
    where: str | None = None,
    dimension_sql: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    if len(refs) == 1:
        return connector.group_count(refs[0], dimensions, where, dimension_sql)
    merged: dict[tuple, int] = {}
    for ref in refs:
        for row in connector.group_count(ref, dimensions, where, dimension_sql):
            key = tuple(row.get(dim) for dim in dimensions)
            merged[key] = merged.get(key, 0) + int(row["row_count"])
    results: list[dict[str, Any]] = []
    for key, count in merged.items():
        item = {dim: key[i] for i, dim in enumerate(dimensions)}
        item["row_count"] = count
        results.append(item)
    return results


def aggregate_concat(
    connector: Connector,
    refs: list[RelationRef],
    expression: str,
    where: str | None = None,
) -> Any:
    if len(refs) == 1:
        return connector.aggregate(refs[0], expression, where)
    total = 0.0
    for ref in refs:
        total += float(connector.aggregate(ref, expression, where) or 0)
    return total


def aggregate_grouped_concat(
    connector: Connector,
    refs: list[RelationRef],
    expression: str,
    group_by: list[str],
    where: str | None = None,
    dimension_sql: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    if len(refs) == 1:
        return connector.aggregate_grouped(
            refs[0], expression, group_by, where, dimension_sql
        )
    merged: dict[tuple, float] = {}
    for ref in refs:
        for row in connector.aggregate_grouped(
            ref, expression, group_by, where, dimension_sql
        ):
            key = tuple(row.get(dim) for dim in group_by)
            merged[key] = merged.get(key, 0.0) + float(row["metric"] or 0)
    results: list[dict[str, Any]] = []
    for key, metric in merged.items():
        item = {dim: key[i] for i, dim in enumerate(group_by)}
        item["metric"] = metric
        results.append(item)
    return results


def inspect_relations(connector: Connector, refs: list[RelationRef]) -> TableInfo:
    """Inspect one or more relations; merge columns (first type wins on conflict)."""
    if len(refs) == 1:
        return connector.inspect_relation(refs[0])
    merged = TableInfo(name=refs[0].name, schema=None)
    for ref in refs:
        info = connector.inspect_relation(ref)
        for name, column in info.columns.items():
            if name not in merged.columns:
                merged.columns[name] = column
    return merged


def missing_columns_on_parts(
    connector: Connector,
    refs: list[RelationRef],
    required: list[str],
) -> dict[str, list[str]]:
    """Return {part_label: [missing columns]} for columns absent from a part."""
    gaps: dict[str, list[str]] = {}
    for index, ref in enumerate(refs):
        info = connector.inspect_relation(ref)
        missing = [col for col in required if col not in info.columns]
        if missing:
            label = ref.table or ref.name or f"part_{index}"
            gaps[str(label)] = missing
    return gaps


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
