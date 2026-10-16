from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from migrate_prove.connectors import RelationRef
from migrate_prove.models import ConnectionConfig
from migrate_prove.salesforce_live import SalesforceConnector
from migrate_prove.secrets import build_connector, describe_connection


def _handler(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if request.method == "POST" and url.endswith("/services/oauth2/token"):
        body = parse_qs(request.content.decode())
        assert body["grant_type"] == ["client_credentials"]
        assert body["client_id"] == ["cid"]
        assert body["client_secret"] == ["csecret"]
        return httpx.Response(
            200,
            json={
                "access_token": "tok-123",
                "instance_url": "https://example.my.salesforce.com",
                "token_type": "Bearer",
            },
        )

    auth = request.headers.get("Authorization")
    assert auth == "Bearer tok-123"

    if "/sobjects/Account/describe" in url:
        return httpx.Response(
            200,
            json={
                "fields": [
                    {"name": "Id", "type": "id", "nillable": False, "length": 18},
                    {"name": "Name", "type": "string", "nillable": True, "length": 80},
                    {"name": "External_Id__c", "type": "string", "nillable": True, "length": 40},
                    {"name": "Status__c", "type": "string", "nillable": True, "length": 20},
                    {"name": "Balance__c", "type": "double", "nillable": True},
                ]
            },
        )

    if "/query" in url and "query/" not in url.split("/services/")[-1]:
        q = parse_qs(urlparse(url).query).get("q", [""])[0]

        # SOQL aggregate pushdown
        if "SELECT COUNT()" in q:
            if "External_Id__c = 'C002'" in q:
                return httpx.Response(200, json={"totalSize": 1, "done": True, "records": []})
            return httpx.Response(200, json={"totalSize": 3, "done": True, "records": []})

        if "COUNT(Id) row_count" in q and "GROUP BY" in q:
            return httpx.Response(
                200,
                json={
                    "done": True,
                    "records": [
                        {
                            "attributes": {"type": "AggregateResult"},
                            "Status__c": "Active",
                            "row_count": 2,
                        },
                        {
                            "attributes": {"type": "AggregateResult"},
                            "Status__c": "Closed",
                            "row_count": 1,
                        },
                    ],
                },
            )

        if "SUM(Balance__c) metric" in q and "GROUP BY" in q:
            return httpx.Response(
                200,
                json={
                    "done": True,
                    "records": [
                        {
                            "attributes": {"type": "AggregateResult"},
                            "Status__c": "Active",
                            "metric": 40.0,
                        },
                        {
                            "attributes": {"type": "AggregateResult"},
                            "Status__c": "Closed",
                            "metric": 20.0,
                        },
                    ],
                },
            )

        if "SUM(Balance__c) metric" in q:
            return httpx.Response(
                200,
                json={
                    "done": True,
                    "records": [
                        {"attributes": {"type": "AggregateResult"}, "metric": 60.0},
                    ],
                },
            )

        if "COUNT(Id) metric" in q:
            return httpx.Response(
                200,
                json={
                    "done": True,
                    "records": [
                        {"attributes": {"type": "AggregateResult"}, "metric": 3},
                    ],
                },
            )

        page1 = [
            {
                "attributes": {"type": "Account"},
                "Id": "001A",
                "Name": "Alpha",
                "External_Id__c": "C001",
                "Status__c": "Active",
                "Balance__c": 10,
            },
            {
                "attributes": {"type": "Account"},
                "Id": "001B",
                "Name": "Beta",
                "External_Id__c": "C002",
                "Status__c": "Closed",
                "Balance__c": 20,
            },
        ]
        if "External_Id__c = 'C002'" in q and "FIELDS(ALL)" in q:
            return httpx.Response(200, json={"done": True, "records": [page1[1]]})
        return httpx.Response(
            200,
            json={
                "done": False,
                "nextRecordsUrl": "/services/data/v59.0/query/next-page-token",
                "records": page1,
            },
        )

    if url.endswith("/query/next-page-token"):
        return httpx.Response(
            200,
            json={
                "done": True,
                "records": [
                    {
                        "attributes": {"type": "Account"},
                        "Id": "001C",
                        "Name": "Gamma",
                        "External_Id__c": "C003",
                        "Status__c": "Active",
                        "Balance__c": 30,
                    }
                ],
            },
        )

    return httpx.Response(404, json={"error": f"unhandled {url}"})


@pytest.fixture
def sf_client():
    transport = httpx.MockTransport(_handler)
    with httpx.Client(transport=transport) as client:
        yield client


def test_salesforce_live_token_describe_and_pagination(sf_client, tmp_path: Path):
    connector = SalesforceConnector(
        login_url="https://login.example.com",
        api_version="59.0",
        client_id="cid",
        client_secret="csecret",
        buffer_rows=2,
        spill_dir=str(tmp_path / "spill"),
        client=sf_client,
    )
    try:
        info = connector.inspect_table("Account")
        assert "External_Id__c" in info.columns
        assert info.columns["Id"].primary_key

        rows = connector.fetch_rows(RelationRef(table="Account"))
        assert [row["External_Id__c"] for row in rows] == ["C001", "C002", "C003"]
        assert all("attributes" not in row for row in rows)

        chunks = list((tmp_path / "spill").glob("chunk-*.ndjson"))
        assert len(chunks) >= 2  # buffer_rows=2 → at least two flushes for 3 rows

        assert connector.count_rows(RelationRef(table="Account")) == 3
        filtered = connector.fetch_rows(
            RelationRef(table="Account"), where="External_Id__c = 'C002'"
        )
        assert len(filtered) == 1
        assert filtered[0]["Name"] == "Beta"
    finally:
        connector.close()


def test_salesforce_soql_aggregate_pushdown(sf_client, tmp_path: Path):
    seen: list[str] = []

    def capturing(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and "/query" in str(request.url):
            q = parse_qs(urlparse(str(request.url)).query).get("q", [""])[0]
            if q:
                seen.append(q)
        return _handler(request)

    transport = httpx.MockTransport(capturing)
    with httpx.Client(transport=transport) as client:
        connector = SalesforceConnector(
            login_url="https://login.example.com",
            api_version="59.0",
            client_id="cid",
            client_secret="csecret",
            spill_dir=str(tmp_path / "spill"),
            client=client,
        )
        try:
            assert connector.count_rows(RelationRef(table="Account")) == 3
            assert any("SELECT COUNT()" in q for q in seen)
            assert not any("FIELDS(ALL)" in q and "COUNT()" in q for q in seen)

            groups = connector.group_count(RelationRef(table="Account"), ["Status__c"])
            by_status = {g["Status__c"]: g["row_count"] for g in groups}
            assert by_status == {"Active": 2, "Closed": 1}
            assert any("GROUP BY Status__c" in q and "row_count" in q for q in seen)

            assert connector.aggregate(RelationRef(table="Account"), "COUNT(*)") == 3.0
            assert connector.aggregate(RelationRef(table="Account"), "SUM(Balance__c)") == 60.0

            grouped = connector.aggregate_grouped(
                RelationRef(table="Account"), "SUM(Balance__c)", ["Status__c"]
            )
            metrics = {row["Status__c"]: row["metric"] for row in grouped}
            assert metrics == {"Active": 40.0, "Closed": 20.0}
        finally:
            connector.close()


def test_salesforce_connection_config_and_describe(tmp_path: Path):
    cfg = ConnectionConfig(
        kind="salesforce",
        url="${SALESFORCE_URL}",
        api_version="${SALESFORCE_API_VERSION}",
        client_id="${SALESFORCE_CLIENT_ID}",
        client_secret="${SALESFORCE_CLIENT_SECRET}",
    )
    env = {
        "SALESFORCE_URL": "https://login.salesforce.com",
        "SALESFORCE_API_VERSION": "59.0",
        "SALESFORCE_CLIENT_ID": "cid",
        "SALESFORCE_CLIENT_SECRET": "super-secret-value",
    }
    label = describe_connection(cfg, tmp_path, env)
    assert label.startswith("salesforce://")
    assert "super-secret-value" not in label
    assert "cid" not in label

    with pytest.raises(ValueError):
        ConnectionConfig(kind="salesforce", url="https://x")

    with pytest.raises(ValueError):
        ConnectionConfig(
            kind="salesforce",
            url="https://x",
            api_version="59.0",
            client_id="a",
            client_secret="b",
            fixtures="fixtures/x",
        )


def test_build_connector_salesforce(tmp_path: Path, sf_client):
    cfg = {
        "kind": "salesforce",
        "url": "https://login.example.com",
        "api_version": "59.0",
        "client_id": "cid",
        "client_secret": "csecret",
        "buffer_rows": 5000,
        "spill_dir": str(tmp_path / "spill"),
    }
    connector = build_connector(cfg, tmp_path, "target")
    assert connector.dialect_name() == "salesforce"
    connector._client = sf_client
    connector._owns_client = False
    try:
        assert connector.count_rows(RelationRef(table="Account")) == 3
    finally:
        connector.close()


def test_soql_query_uses_fields_all_for_v51_plus(sf_client, tmp_path: Path):
    seen: list[str] = []

    def capturing(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and "/query" in str(request.url) and "query/" not in str(
            request.url
        ).split("/services/")[-1]:
            seen.append(str(request.url))
        return _handler(request)

    transport = httpx.MockTransport(capturing)
    with httpx.Client(transport=transport) as client:
        connector = SalesforceConnector(
            login_url="https://login.example.com",
            api_version="v59.0",
            client_id="cid",
            client_secret="csecret",
            spill_dir=str(tmp_path / "spill"),
            client=client,
        )
        try:
            connector.fetch_rows(RelationRef(table="Account"))
        finally:
            connector.close()
    assert seen
    q = parse_qs(urlparse(seen[0]).query)["q"][0]
    assert "FIELDS(ALL)" in q
