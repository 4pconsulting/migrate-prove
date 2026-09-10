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


class Connector(Protocol):
    name: str

    def inspect_table(self, table: str, schema: str | None = None) -> TableInfo: ...

    def query(self, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]: ...

    def scalar(self, sql: str, params: dict[str, Any] | None = None) -> Any: ...

    def qualified(self, table: str, schema: str | None = None) -> str: ...

    def quote(self, ident: str) -> str: ...

    def dialect_name(self) -> str: ...


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
