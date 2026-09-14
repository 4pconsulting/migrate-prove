from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from migrate_prove.connectors import ColumnInfo, RelationRef, TableInfo

_SUM_RE = re.compile(r"^\s*SUM\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)\s*$", re.I)
_COUNT_RE = re.compile(r"^\s*COUNT\s*\(\s*\*\s*\)\s*$", re.I)


def project_salesforce_record(record: dict[str, Any]) -> dict[str, Any]:
    """Strip Salesforce envelope fields and flatten a query record."""
    return {key: value for key, value in record.items() if key != "attributes"}


def _parse_simple_filter(where: str | None) -> list[tuple[str, str, Any]]:
    """Parse a tiny SOQL-ish AND of `Field = 'value'` / `Field = 1` predicates."""
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
                f"Salesforce mock only supports simple AND equality filters, got: {part!r}"
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


def _row_matches(row: dict[str, Any], predicates: list[tuple[str, str, Any]]) -> bool:
    for field, op, value in predicates:
        actual = row.get(field)
        if op == "=" and actual != value:
            return False
        if op == "!=" and actual == value:
            return False
    return True


def _eval_aggregate(expression: str, rows: list[dict[str, Any]]) -> float:
    if _COUNT_RE.match(expression):
        return float(len(rows))
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
        f"Salesforce mock aggregates support COUNT(*) or SUM(field) only, got: {expression!r}"
    )


class SalesforceMockConnector:
    """Fixture-backed Salesforce connector (Describe + paginated query JSON).

    Layout::

        fixtures/
          describe/Account.json
          query/Account.page1.json
          query/Account.page2.json
    """

    def __init__(self, fixtures_dir: str | Path, name: str = "salesforce") -> None:
        self.name = name
        self.fixtures_dir = Path(fixtures_dir)
        if not self.fixtures_dir.is_dir():
            raise FileNotFoundError(f"Salesforce fixtures directory not found: {self.fixtures_dir}")

    def dialect_name(self) -> str:
        return "salesforce"

    def quote(self, ident: str) -> str:
        return ident

    def qualified(self, table: str, schema: str | None = None) -> str:
        del schema
        return table

    def relation_sql(self, ref: RelationRef) -> str:
        if ref.sql is not None:
            raise ValueError("Salesforce connector does not support inline SQL relations")
        assert ref.table is not None
        return ref.table

    def _load_json(self, path: Path) -> Any:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)

    def inspect_table(self, table: str, schema: str | None = None) -> TableInfo:
        del schema
        path = self.fixtures_dir / "describe" / f"{table}.json"
        if not path.exists():
            raise FileNotFoundError(f"Missing Salesforce describe fixture: {path}")
        payload = self._load_json(path)
        info = TableInfo(name=table, schema=None)
        for field in payload.get("fields", []):
            name = field["name"]
            length = field.get("length")
            nillable = field.get("nillable", True)
            info.columns[name] = ColumnInfo(
                name=name,
                type=str(field.get("type", "string")),
                nullable=bool(nillable),
                primary_key=str(field.get("type", "")) == "id",
                max_length=int(length) if length else None,
            )
        return info

    def inspect_relation(self, ref: RelationRef) -> TableInfo:
        if ref.sql is not None:
            raise ValueError("Salesforce connector does not support inline SQL relations")
        assert ref.table is not None
        return self.inspect_table(ref.table)

    def _page_paths(self, object_name: str) -> list[Path]:
        query_dir = self.fixtures_dir / "query"
        pages = sorted(query_dir.glob(f"{object_name}.page*.json"))
        if pages:
            return pages
        single = query_dir / f"{object_name}.json"
        if single.exists():
            return [single]
        raise FileNotFoundError(
            f"No Salesforce query fixtures for {object_name!r} under {query_dir}"
        )

    def _load_all_records(self, object_name: str) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        pages = self._page_paths(object_name)
        for index, path in enumerate(pages):
            payload = self._load_json(path)
            chunk = [project_salesforce_record(row) for row in payload.get("records", [])]
            records.extend(chunk)
            done = bool(payload.get("done", index == len(pages) - 1))
            next_url = payload.get("nextRecordsUrl")
            if not done and index == len(pages) - 1 and next_url:
                raise FileNotFoundError(
                    f"Fixture {path.name} has nextRecordsUrl={next_url!r} but no further page file"
                )
        return records

    def fetch_rows(self, ref: RelationRef, where: str | None = None) -> list[dict[str, Any]]:
        object_name = self.relation_sql(ref)
        predicates = _parse_simple_filter(where)
        rows = self._load_all_records(object_name)
        if not predicates:
            return rows
        return [row for row in rows if _row_matches(row, predicates)]

    def count_rows(self, ref: RelationRef, where: str | None = None) -> int:
        return len(self.fetch_rows(ref, where))

    def group_count(
        self,
        ref: RelationRef,
        dimensions: list[str],
        where: str | None = None,
        dimension_sql: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        del dimension_sql
        rows = self.fetch_rows(ref, where)
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

    def aggregate(self, ref: RelationRef, expression: str, where: str | None = None) -> Any:
        return _eval_aggregate(expression, self.fetch_rows(ref, where))

    def aggregate_grouped(
        self,
        ref: RelationRef,
        expression: str,
        group_by: list[str],
        where: str | None = None,
        dimension_sql: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        del dimension_sql
        rows = self.fetch_rows(ref, where)
        buckets: dict[tuple, list[dict[str, Any]]] = {}
        for row in rows:
            key = tuple(row.get(dim) for dim in group_by)
            buckets.setdefault(key, []).append(row)
        results: list[dict[str, Any]] = []
        for key, group_rows in buckets.items():
            item = {dim: key[i] for i, dim in enumerate(group_by)}
            item["metric"] = _eval_aggregate(expression, group_rows)
            results.append(item)
        return results

    def query(self, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        del params
        raise NotImplementedError(
            f"Raw SQL/SOQL passthrough is not supported on SalesforceMockConnector: {sql!r}"
        )

    def scalar(self, sql: str, params: dict[str, Any] | None = None) -> Any:
        del params
        raise NotImplementedError(
            f"Raw SQL/SOQL passthrough is not supported on SalesforceMockConnector: {sql!r}"
        )
