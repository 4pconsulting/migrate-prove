from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from migrate_prove.api_rows import (
    aggregate_grouped_rows,
    eval_aggregate,
    filter_rows,
    group_count_rows,
    project_salesforce_record,
)
from migrate_prove.connectors import ColumnInfo, RelationRef, TableInfo

# Re-export for older imports / demos.
__all__ = ["SalesforceMockConnector", "project_salesforce_record"]


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
        return filter_rows(self._load_all_records(object_name), where)

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
        return group_count_rows(self.fetch_rows(ref, where), dimensions)

    def aggregate(self, ref: RelationRef, expression: str, where: str | None = None) -> Any:
        return eval_aggregate(expression, self.fetch_rows(ref, where))

    def aggregate_grouped(
        self,
        ref: RelationRef,
        expression: str,
        group_by: list[str],
        where: str | None = None,
        dimension_sql: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        del dimension_sql
        return aggregate_grouped_rows(self.fetch_rows(ref, where), expression, group_by)

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
