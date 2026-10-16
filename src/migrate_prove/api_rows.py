"""Shared in-process row helpers for API connectors (Salesforce mock/live, HTTP)."""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from typing import Any

_SUM_RE = re.compile(r"^\s*SUM\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)\s*$", re.I)
_COUNT_RE = re.compile(r"^\s*COUNT\s*\(\s*\*\s*\)\s*$", re.I)


def project_salesforce_record(record: dict[str, Any]) -> dict[str, Any]:
    """Strip Salesforce envelope fields and flatten a query record."""
    return {key: value for key, value in record.items() if key != "attributes"}


def project_http_record(record: dict[str, Any]) -> dict[str, Any]:
    """Keep scalar top-level fields; drop nested objects/lists."""
    flat: dict[str, Any] = {}
    for key, value in record.items():
        if isinstance(value, (dict, list)):
            continue
        flat[key] = value
    return flat


def parse_simple_filter(where: str | None) -> list[tuple[str, str, Any]]:
    """Parse a tiny AND of `Field = 'value'` / `Field = 1` predicates."""
    if not where or not where.strip():
        return []
    clauses: list[tuple[str, str, Any]] = []
    for part in re.split(r"\s+AND\s+", where.strip(), flags=re.I):
        match = re.match(
            r"^([A-Za-z_][A-Za-z0-9_]*)\s*(=|!=)\s*(.+)$",
            part.strip(),
        )
        if not match:
            raise ValueError(
                f"API connectors only support simple AND equality filters, got: {part!r}"
            )
        field, op, raw = match.group(1), match.group(2), match.group(3).strip()
        if (raw.startswith("'") and raw.endswith("'")) or (
            raw.startswith('"') and raw.endswith('"')
        ):
            value: Any = raw[1:-1]
        elif raw.lower() == "null":
            value = None
        elif re.fullmatch(r"-?\d+", raw):
            value = int(raw)
        elif re.fullmatch(r"-?\d+\.\d+", raw):
            value = float(raw)
        else:
            value = raw
        clauses.append((field, op, value))
    return clauses


def filter_to_soql_where(where: str | None) -> str | None:
    """Validate and return a SOQL WHERE fragment (same grammar as parse_simple_filter)."""
    predicates = parse_simple_filter(where)
    if not predicates:
        return None
    parts: list[str] = []
    for field, op, value in predicates:
        if value is None:
            literal = "null"
        elif isinstance(value, str):
            escaped = value.replace("\\", "\\\\").replace("'", "\\'")
            literal = f"'{escaped}'"
        elif isinstance(value, bool):
            literal = "true" if value else "false"
        else:
            literal = str(value)
        parts.append(f"{field} {op} {literal}")
    return " AND ".join(parts)


def row_matches(row: dict[str, Any], predicates: list[tuple[str, str, Any]]) -> bool:
    for field, op, value in predicates:
        actual = row.get(field)
        if op == "=" and actual != value:
            return False
        if op == "!=" and actual == value:
            return False
    return True


def filter_rows(
    rows: Iterable[dict[str, Any]], where: str | None
) -> list[dict[str, Any]]:
    predicates = parse_simple_filter(where)
    if not predicates:
        return list(rows)
    return [row for row in rows if row_matches(row, predicates)]


def eval_aggregate(expression: str, rows: Iterable[dict[str, Any]]) -> float:
    if _COUNT_RE.match(expression):
        return float(sum(1 for _ in rows))
    match = _SUM_RE.match(expression)
    if match:
        field = match.group(1)
        total = 0.0
        for row in rows:
            value = row.get(field)
            if value is not None:
                total += float(value)
        return total
    raise ValueError(
        f"API aggregates support COUNT(*) or SUM(field) only, got: {expression!r}"
    )


def group_count_rows(
    rows: Iterable[dict[str, Any]],
    dimensions: list[str],
) -> list[dict[str, Any]]:
    buckets: dict[tuple, int] = {}
    for row in rows:
        key = tuple(row.get(dim) for dim in dimensions)
        buckets[key] = buckets.get(key, 0) + 1
    results: list[dict[str, Any]] = []
    for key, count in buckets.items():
        item = {dim: key[i] for i, dim in enumerate(dimensions)}
        item["row_count"] = count
        results.append(item)
    return results


def aggregate_grouped_rows(
    rows: Iterable[dict[str, Any]],
    expression: str,
    group_by: list[str],
) -> list[dict[str, Any]]:
    buckets: dict[tuple, list[dict[str, Any]]] = {}
    for row in rows:
        key = tuple(row.get(dim) for dim in group_by)
        buckets.setdefault(key, []).append(row)
    results: list[dict[str, Any]] = []
    for key, group_rows in buckets.items():
        item = {dim: key[i] for i, dim in enumerate(group_by)}
        item["metric"] = eval_aggregate(expression, group_rows)
        results.append(item)
    return results


def iter_filtered(
    rows: Iterable[dict[str, Any]], where: str | None
) -> Iterator[dict[str, Any]]:
    predicates = parse_simple_filter(where)
    if not predicates:
        yield from rows
        return
    for row in rows:
        if row_matches(row, predicates):
            yield row
