"""Live Salesforce REST connector (OAuth2 client-credentials + query pagination)."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote, urljoin

from migrate_prove.api_rows import (
    aggregate_grouped_rows,
    eval_aggregate,
    filter_rows,
    filter_to_soql_where,
    group_count_rows,
    project_salesforce_record,
)
from migrate_prove.connectors import ColumnInfo, RelationRef, TableInfo
from migrate_prove.http_util import DEFAULT_TIMEOUT, request_json, require_httpx
from migrate_prove.row_store import DEFAULT_BUFFER_ROWS, SpilledRowStore


def _normalize_api_version(version: str) -> str:
    version = version.strip()
    if version.lower().startswith("v"):
        version = version[1:]
    return version


def _parse_api_major(version: str) -> float:
    try:
        return float(_normalize_api_version(version))
    except ValueError:
        return 0.0


class SalesforceConnector:
    """Live Salesforce connector using client-credentials OAuth and disk-spilled query pages."""

    def __init__(
        self,
        *,
        login_url: str,
        api_version: str,
        client_id: str,
        client_secret: str,
        name: str = "salesforce",
        buffer_rows: int = DEFAULT_BUFFER_ROWS,
        spill_dir: str | None = None,
        max_rows: int | None = None,
        client: Any | None = None,
    ) -> None:
        self.name = name
        self.login_url = login_url.rstrip("/")
        self.api_version = _normalize_api_version(api_version)
        self.client_id = client_id
        self.client_secret = client_secret
        self.buffer_rows = buffer_rows
        self.spill_dir = spill_dir
        self.max_rows = max_rows
        httpx = require_httpx()
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=DEFAULT_TIMEOUT)
        self._access_token: str | None = None
        self._instance_url: str | None = None
        self._describe_cache: dict[str, TableInfo] = {}
        self._stores: list[SpilledRowStore] = []

    def close(self) -> None:
        for store in self._stores:
            store.cleanup()
        self._stores.clear()
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> SalesforceConnector:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

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

    def _ensure_token(self) -> tuple[str, str]:
        if self._access_token and self._instance_url:
            return self._access_token, self._instance_url
        token_url = f"{self.login_url}/services/oauth2/token"
        payload = request_json(
            self._client,
            "POST",
            token_url,
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
        )
        if not isinstance(payload, dict):
            raise RuntimeError("Salesforce token response was not a JSON object")
        token = payload.get("access_token")
        instance = payload.get("instance_url")
        if not token or not instance:
            raise RuntimeError(
                "Salesforce token response missing access_token or instance_url"
            )
        self._access_token = str(token)
        self._instance_url = str(instance).rstrip("/")
        return self._access_token, self._instance_url

    def _auth_headers(self) -> dict[str, str]:
        token, _ = self._ensure_token()
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }

    def _api_root(self) -> str:
        _, instance = self._ensure_token()
        return f"{instance}/services/data/v{self.api_version}"

    def _absolute_url(self, path_or_url: str) -> str:
        _, instance = self._ensure_token()
        if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
            return path_or_url
        return urljoin(instance + "/", path_or_url.lstrip("/"))

    def inspect_table(self, table: str, schema: str | None = None) -> TableInfo:
        del schema
        if table in self._describe_cache:
            return self._describe_cache[table]
        url = f"{self._api_root()}/sobjects/{quote(table, safe='')}/describe"
        payload = request_json(self._client, "GET", url, headers=self._auth_headers())
        if not isinstance(payload, dict):
            raise RuntimeError(f"Unexpected describe payload for {table}")
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
        self._describe_cache[table] = info
        return info

    def inspect_relation(self, ref: RelationRef) -> TableInfo:
        if ref.sql is not None:
            raise ValueError("Salesforce connector does not support inline SQL relations")
        assert ref.table is not None
        return self.inspect_table(ref.table)

    def _select_clause(self, object_name: str) -> str:
        if _parse_api_major(self.api_version) >= 51.0:
            return "FIELDS(ALL)"
        info = self.inspect_table(object_name)
        names = sorted(info.columns)
        if not names:
            raise ValueError(f"No fields found in describe for {object_name}")
        # Salesforce SOQL SELECT length / field count limits; keep a sane cap.
        if len(names) > 200:
            names = names[:200]
        return ", ".join(names)

    def _query_into_store(
        self, object_name: str, where: str | None
    ) -> SpilledRowStore:
        soql_where = filter_to_soql_where(where)
        select_clause = self._select_clause(object_name)
        soql = f"SELECT {select_clause} FROM {object_name}"
        if soql_where:
            soql = f"{soql} WHERE {soql_where}"
        safe = " ()',=!.<>_"
        url = f"{self._api_root()}/query?q={quote(soql, safe=safe)}"
        store = SpilledRowStore(
            buffer_rows=self.buffer_rows,
            spill_dir=self.spill_dir,
            cleanup=self.spill_dir is None,
            max_rows=self.max_rows,
        )
        self._stores.append(store)
        headers = self._auth_headers()
        while url:
            payload = request_json(self._client, "GET", url, headers=headers)
            if not isinstance(payload, dict):
                raise RuntimeError("Unexpected Salesforce query payload")
            records = [
                project_salesforce_record(row) for row in payload.get("records", [])
            ]
            store.extend(records)
            if payload.get("done", True):
                break
            next_url = payload.get("nextRecordsUrl")
            if not next_url:
                break
            url = self._absolute_url(str(next_url))
        store.close()
        return store

    def fetch_rows(self, ref: RelationRef, where: str | None = None) -> list[dict[str, Any]]:
        object_name = self.relation_sql(ref)
        # Push filter into SOQL; also apply in-process for consistent client semantics.
        store = self._query_into_store(object_name, where)
        return filter_rows(store.iter_rows(), where)

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
            f"Raw SQL/SOQL passthrough is not supported on SalesforceConnector: {sql!r}"
        )

    def scalar(self, sql: str, params: dict[str, Any] | None = None) -> Any:
        del params
        raise NotImplementedError(
            f"Raw SQL/SOQL passthrough is not supported on SalesforceConnector: {sql!r}"
        )
