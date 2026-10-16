from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from migrate_prove.connectors import RelationRef
from migrate_prove.http_connector import HttpConnector
from migrate_prove.models import ConnectionConfig
from migrate_prove.secrets import build_connector, describe_connection


def _handler(request: httpx.Request) -> httpx.Response:
    assert request.headers.get("Authorization") == "Bearer secret-token"
    path = request.url.path

    if path == "/v1/accounts/schema":
        return httpx.Response(
            200,
            json={
                "fields": [
                    {"name": "id", "type": "string", "nillable": False},
                    {"name": "name", "type": "string", "nillable": True},
                    {"name": "status", "type": "string", "nillable": True},
                ]
            },
        )

    if path == "/v1/accounts":
        if request.url.params.get("page") == "2":
            return httpx.Response(
                200,
                json={
                    "data": [{"id": "3", "name": "C", "status": "Active"}],
                    "next": None,
                },
            )
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": "1", "name": "A", "status": "Active", "nested": {"x": 1}},
                    {"id": "2", "name": "B", "status": "Closed"},
                ],
                "next": "/v1/accounts?page=2",
            },
        )

    return httpx.Response(404, json={"error": path})


def test_http_pagination_auth_and_projection(tmp_path: Path):
    transport = httpx.MockTransport(_handler)
    with httpx.Client(transport=transport, base_url="https://api.example.com/") as client:
        connector = HttpConnector(
            base_url="https://api.example.com",
            auth={"type": "bearer", "token": "secret-token"},
            resources={
                "Account": {
                    "list_path": "/v1/accounts",
                    "schema_path": "/v1/accounts/schema",
                    "records_jmespath": "data",
                    "next_jmespath": "next",
                }
            },
            buffer_rows=2,
            spill_dir=str(tmp_path / "spill"),
            client=client,
        )
        try:
            assert connector.dialect_name() == "http"
            info = connector.inspect_table("Account")
            assert set(info.columns) == {"id", "name", "status"}

            rows = connector.fetch_rows(RelationRef(table="Account"))
            assert [row["id"] for row in rows] == ["1", "2", "3"]
            assert "nested" not in rows[0]

            filtered = connector.fetch_rows(
                RelationRef(table="Account"), where="status = 'Active'"
            )
            assert [row["id"] for row in filtered] == ["1", "3"]

            groups = connector.group_count(RelationRef(table="Account"), ["status"])
            by_status = {g["status"]: g["row_count"] for g in groups}
            assert by_status["Active"] == 2
            assert by_status["Closed"] == 1

            assert connector.aggregate(RelationRef(table="Account"), "COUNT(*)") == 3.0
            assert len(list((tmp_path / "spill").glob("chunk-*.ndjson"))) >= 1
        finally:
            connector.close()


def test_http_schema_infer_without_schema_path(tmp_path: Path):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("Authorization") == "Bearer t"
        return httpx.Response(
            200,
            json={"items": [{"code": "X", "amount": 1.5, "ok": True}], "next": None},
        )

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport, base_url="https://api.example.com/") as client:
        connector = HttpConnector(
            base_url="https://api.example.com",
            auth={"type": "bearer", "token": "t"},
            resources={
                "Thing": {
                    "list_path": "/things",
                    "records_jmespath": "items",
                    "next_jmespath": "next",
                }
            },
            client=client,
        )
        try:
            info = connector.inspect_table("Thing")
            assert info.columns["code"].type == "string"
            assert info.columns["amount"].type == "number"
            assert info.columns["ok"].type == "boolean"
        finally:
            connector.close()


def test_http_connection_config_and_describe(tmp_path: Path):
    cfg = ConnectionConfig(
        kind="http",
        base_url="${API_BASE_URL}",
        auth={"type": "bearer", "token": "${API_TOKEN}"},
        resources={"Account": {"list_path": "/v1/accounts"}},
    )
    env = {"API_BASE_URL": "https://api.example.com", "API_TOKEN": "sekrit"}
    label = describe_connection(cfg, tmp_path, env)
    assert "api.example.com" in label
    assert "sekrit" not in label

    with pytest.raises(ValueError):
        ConnectionConfig(kind="http", base_url="https://x")

    connector = build_connector(
        {
            "kind": "http",
            "base_url": "https://api.example.com",
            "auth": {"type": "bearer", "token": "secret-token"},
            "resources": {
                "Account": {
                    "list_path": "/v1/accounts",
                    "records_jmespath": "data",
                    "next_jmespath": "next",
                }
            },
        },
        tmp_path,
        "target",
    )
    transport = httpx.MockTransport(_handler)
    connector._client = httpx.Client(
        transport=transport, base_url="https://api.example.com/"
    )
    connector._owns_client = True
    try:
        assert connector.count_rows(RelationRef(table="Account")) == 3
    finally:
        connector.close()
