"""Declarative raw HTTP API connector with JMESPath pagination and disk spill."""

from __future__ import annotations

from typing import Any, Literal
from urllib.parse import urljoin

from pydantic import BaseModel, ConfigDict, Field

from migrate_prove.api_rows import (
    aggregate_grouped_rows,
    eval_aggregate,
    filter_rows,
    group_count_rows,
    project_http_record,
)
from migrate_prove.connectors import ColumnInfo, RelationRef, TableInfo
from migrate_prove.http_util import DEFAULT_TIMEOUT, request_json, require_httpx
from migrate_prove.row_store import DEFAULT_BUFFER_ROWS, SpilledRowStore


class HttpAuthConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["bearer", "header", "basic"] = "bearer"
    token: str | None = None
    header_name: str | None = None
    header_value: str | None = None
    username: str | None = None
    password: str | None = None


class HttpResourceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    list_path: str
    schema_path: str | None = None
    records_jmespath: str = "data"
    next_jmespath: str | None = "next"
    headers: dict[str, str] = Field(default_factory=dict)


def require_jmespath():
    try:
        import jmespath
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "HTTP connectors require the 'http' extra. "
            "Install with: pip install 'migrate-prove[http]'"
        ) from exc
    return jmespath


def _infer_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    return "string"


class HttpConnector:
    """Declarative REST connector: list + optional schema endpoints → flat dict rows."""

    def __init__(
        self,
        *,
        base_url: str,
        resources: dict[str, HttpResourceConfig | dict[str, Any]],
        auth: HttpAuthConfig | dict[str, Any] | None = None,
        name: str = "http",
        buffer_rows: int = DEFAULT_BUFFER_ROWS,
        spill_dir: str | None = None,
        max_rows: int | None = None,
        client: Any | None = None,
    ) -> None:
        self.name = name
        self.base_url = base_url.rstrip("/") + "/"
        self.auth = (
            auth
            if isinstance(auth, HttpAuthConfig)
            else HttpAuthConfig.model_validate(auth or {"type": "bearer"})
        )
        self.resources = {
            key: (
                value
                if isinstance(value, HttpResourceConfig)
                else HttpResourceConfig.model_validate(value)
            )
            for key, value in resources.items()
        }
        self.buffer_rows = buffer_rows
        self.spill_dir = spill_dir
        self.max_rows = max_rows
        httpx = require_httpx()
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=DEFAULT_TIMEOUT, base_url=self.base_url)
        self._stores: list[SpilledRowStore] = []
        self._schema_cache: dict[str, TableInfo] = {}

    def close(self) -> None:
        for store in self._stores:
            store.cleanup()
        self._stores.clear()
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> HttpConnector:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def dialect_name(self) -> str:
        return "http"

    def quote(self, ident: str) -> str:
        return ident

    def qualified(self, table: str, schema: str | None = None) -> str:
        del schema
        return table

    def relation_sql(self, ref: RelationRef) -> str:
        if ref.sql is not None:
            raise ValueError("HTTP connector does not support inline SQL relations")
        assert ref.table is not None
        return ref.table

    def _resource(self, name: str) -> HttpResourceConfig:
        if name not in self.resources:
            raise KeyError(
                f"No HTTP resource configured for {name!r}. "
                f"Known resources: {sorted(self.resources)}"
            )
        return self.resources[name]

    def _auth_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Accept": "application/json"}
        if self.auth.type == "bearer":
            if not self.auth.token:
                raise ValueError("HTTP bearer auth requires `token`")
            headers["Authorization"] = f"Bearer {self.auth.token}"
        elif self.auth.type == "header":
            if not self.auth.header_name or self.auth.header_value is None:
                raise ValueError("HTTP header auth requires header_name and header_value")
            headers[self.auth.header_name] = self.auth.header_value
        return headers

    def _auth_tuple(self) -> tuple[str, str] | None:
        if self.auth.type == "basic":
            if not self.auth.username:
                raise ValueError("HTTP basic auth requires username")
            return self.auth.username, self.auth.password or ""
        return None

    def _resolve_url(self, path_or_url: str) -> str:
        if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
            return path_or_url
        return urljoin(self.base_url, path_or_url.lstrip("/"))

    def _get_json(self, path_or_url: str, extra_headers: dict[str, str] | None = None) -> Any:
        headers = self._auth_headers()
        if extra_headers:
            headers.update(extra_headers)
        return request_json(
            self._client,
            "GET",
            self._resolve_url(path_or_url),
            headers=headers,
            auth=self._auth_tuple(),
        )

    def inspect_table(self, table: str, schema: str | None = None) -> TableInfo:
        del schema
        if table in self._schema_cache:
            return self._schema_cache[table]
        resource = self._resource(table)
        if resource.schema_path:
            payload = self._get_json(resource.schema_path, resource.headers)
            info = TableInfo(name=table, schema=None)
            if isinstance(payload, dict):
                for field in payload.get("fields", []):
                    name = field["name"]
                    length = field.get("length")
                    info.columns[name] = ColumnInfo(
                        name=name,
                        type=str(field.get("type", "string")),
                        nullable=bool(field.get("nillable", True)),
                        primary_key=bool(field.get("primary_key", False)),
                        max_length=int(length) if length else None,
                    )
            self._schema_cache[table] = info
            return info

        # Infer from first list page(s).
        rows = self._list_into_store(table, max_infer=50).list_rows()
        info = TableInfo(name=table, schema=None)
        for row in rows:
            for key, value in row.items():
                if key not in info.columns:
                    info.columns[key] = ColumnInfo(
                        name=key,
                        type=_infer_type(value),
                        nullable=True,
                    )
        self._schema_cache[table] = info
        return info

    def inspect_relation(self, ref: RelationRef) -> TableInfo:
        if ref.sql is not None:
            raise ValueError("HTTP connector does not support inline SQL relations")
        assert ref.table is not None
        return self.inspect_table(ref.table)

    def _list_into_store(
        self,
        object_name: str,
        *,
        max_infer: int | None = None,
    ) -> SpilledRowStore:
        jmespath = require_jmespath()
        resource = self._resource(object_name)
        store = SpilledRowStore(
            buffer_rows=self.buffer_rows,
            spill_dir=self.spill_dir,
            cleanup=self.spill_dir is None,
            max_rows=None if max_infer is not None else self.max_rows,
        )
        self._stores.append(store)
        url: str | None = resource.list_path
        while url:
            payload = self._get_json(url, resource.headers)
            records_raw = jmespath.search(resource.records_jmespath, payload)
            if records_raw is None:
                records_raw = []
            if not isinstance(records_raw, list):
                raise ValueError(
                    f"records_jmespath {resource.records_jmespath!r} did not return a list"
                )
            projected = [
                project_http_record(row)
                for row in records_raw
                if isinstance(row, dict)
            ]
            for row in projected:
                if max_infer is not None and store.total_rows >= max_infer:
                    break
                store.append(row)
            if max_infer is not None and store.total_rows >= max_infer:
                break
            if not resource.next_jmespath:
                break
            next_url = jmespath.search(resource.next_jmespath, payload)
            if not next_url:
                break
            url = str(next_url)
        store.close()
        return store

    def fetch_rows(self, ref: RelationRef, where: str | None = None) -> list[dict[str, Any]]:
        object_name = self.relation_sql(ref)
        store = self._list_into_store(object_name)
        if where:
            return filter_rows(store.iter_rows(), where)
        return store.list_rows()

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
            f"Raw SQL passthrough is not supported on HttpConnector: {sql!r}"
        )

    def scalar(self, sql: str, params: dict[str, Any] | None = None) -> Any:
        del params
        raise NotImplementedError(
            f"Raw SQL passthrough is not supported on HttpConnector: {sql!r}"
        )
