from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine


@dataclass
class ColumnInfo:
    name: str
    type: str
    nullable: bool
    primary_key: bool = False
    max_length: int | None = None


@dataclass
class TableInfo:
    name: str
    schema: str | None
    columns: dict[str, ColumnInfo] = field(default_factory=dict)

    def has_column(self, name: str) -> bool:
        return name in self.columns


@dataclass(frozen=True)
class RelationRef:
    """A physical table/schema or an inline SQL relation (CTE / subquery)."""

    table: str | None = None
    schema: str | None = None
    sql: str | None = None
    name: str = "_rel"

    def __post_init__(self) -> None:
        has_table = self.table is not None
        has_sql = self.sql is not None
        if has_table == has_sql:
            raise ValueError("RelationRef requires exactly one of table or sql")


def where_clause(clause: str | None) -> str:
    return f" WHERE {clause}" if clause else ""


class Connector(Protocol):
    name: str

    def inspect_table(self, table: str, schema: str | None = None) -> TableInfo: ...

    def inspect_relation(self, ref: RelationRef) -> TableInfo: ...

    def query(self, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]: ...

    def scalar(self, sql: str, params: dict[str, Any] | None = None) -> Any: ...

    def qualified(self, table: str, schema: str | None = None) -> str: ...

    def quote(self, ident: str) -> str: ...

    def dialect_name(self) -> str: ...

    def relation_sql(self, ref: RelationRef) -> str: ...

    def fetch_rows(
        self, ref: RelationRef, where: str | None = None
    ) -> list[dict[str, Any]]: ...

    def count_rows(self, ref: RelationRef, where: str | None = None) -> int: ...

    def group_count(
        self,
        ref: RelationRef,
        dimensions: list[str],
        where: str | None = None,
        dimension_sql: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]: ...

    def aggregate(
        self, ref: RelationRef, expression: str, where: str | None = None
    ) -> Any: ...

    def aggregate_grouped(
        self,
        ref: RelationRef,
        expression: str,
        group_by: list[str],
        where: str | None = None,
        dimension_sql: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]: ...


class SqlAlchemyConnector:
    def __init__(self, url: str, name: str = "db") -> None:
        self.name = name
        self.engine: Engine = create_engine(url)
        self._preparer = self.engine.dialect.identifier_preparer

    def dialect_name(self) -> str:
        return self.engine.dialect.name

    def quote(self, ident: str) -> str:
        return self._preparer.quote(ident)

    def qualified(self, table: str, schema: str | None = None) -> str:
        quoted_table = self.quote(table)
        if schema:
            return f"{self.quote(schema)}.{quoted_table}"
        return quoted_table

    def relation_sql(self, ref: RelationRef) -> str:
        if ref.sql is not None:
            return f"({ref.sql}) AS {self.quote(ref.name)}"
        assert ref.table is not None
        return self.qualified(ref.table, ref.schema)

    def inspect_table(self, table: str, schema: str | None = None) -> TableInfo:
        inspector = inspect(self.engine)
        columns = inspector.get_columns(table, schema=schema)
        pk = inspector.get_pk_constraint(table, schema=schema)
        pk_cols = set(pk.get("constrained_columns") or [])
        info = TableInfo(name=table, schema=schema)
        for column in columns:
            col_type = column["type"]
            max_length = getattr(col_type, "length", None)
            info.columns[column["name"]] = ColumnInfo(
                name=column["name"],
                type=str(col_type),
                nullable=bool(column.get("nullable", True)),
                primary_key=column["name"] in pk_cols,
                max_length=max_length,
            )
        return info

    def inspect_relation(self, ref: RelationRef) -> TableInfo:
        if ref.sql is None:
            assert ref.table is not None
            return self.inspect_table(ref.table, ref.schema)
        sql = f"SELECT * FROM {self.relation_sql(ref)} LIMIT 0"
        with self.engine.connect() as connection:
            result = connection.execute(text(sql))
            keys = list(result.keys())
        info = TableInfo(name=ref.name, schema=None)
        for key in keys:
            info.columns[str(key)] = ColumnInfo(
                name=str(key),
                type="unknown",
                nullable=True,
            )
        return info

    def query(self, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        with self.engine.connect() as connection:
            result = connection.execute(text(sql), params or {})
            return [dict(row) for row in result.mappings()]

    def scalar(self, sql: str, params: dict[str, Any] | None = None) -> Any:
        with self.engine.connect() as connection:
            return connection.execute(text(sql), params or {}).scalar()

    def execute(self, sql: str, params: dict[str, Any] | None = None) -> None:
        with self.engine.begin() as connection:
            connection.execute(text(sql), params or {})

    def fetch_rows(self, ref: RelationRef, where: str | None = None) -> list[dict[str, Any]]:
        return self.query(f"SELECT * FROM {self.relation_sql(ref)}{where_clause(where)}")

    def count_rows(self, ref: RelationRef, where: str | None = None) -> int:
        return int(self.scalar(f"SELECT COUNT(*) FROM {self.relation_sql(ref)}{where_clause(where)}") or 0)

    def group_count(
        self,
        ref: RelationRef,
        dimensions: list[str],
        where: str | None = None,
        dimension_sql: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        selects: list[str] = []
        groups: list[str] = []
        for index, dimension in enumerate(dimensions, start=1):
            expr = (dimension_sql or {}).get(dimension, self.quote(dimension))
            alias = self.quote(dimension)
            selects.append(f"{expr} AS {alias}")
            groups.append(str(index))
        select_sql = ", ".join(selects + ["COUNT(*) AS row_count"])
        group_sql = ", ".join(groups)
        sql = (
            f"SELECT {select_sql} FROM {self.relation_sql(ref)}"
            f"{where_clause(where)} GROUP BY {group_sql}"
        )
        return self.query(sql)

    def aggregate(self, ref: RelationRef, expression: str, where: str | None = None) -> Any:
        return self.scalar(
            f"SELECT {expression} FROM {self.relation_sql(ref)}{where_clause(where)}"
        )

    def aggregate_grouped(
        self,
        ref: RelationRef,
        expression: str,
        group_by: list[str],
        where: str | None = None,
        dimension_sql: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        selects = [
            f"{(dimension_sql or {}).get(dim, self.quote(dim))} AS {self.quote(dim)}"
            for dim in group_by
        ]
        sql = (
            f"SELECT {', '.join(selects)}, {expression} AS metric "
            f"FROM {self.relation_sql(ref)}{where_clause(where)} "
            f"GROUP BY {', '.join(str(i) for i in range(1, len(group_by) + 1))}"
        )
        return self.query(sql)
