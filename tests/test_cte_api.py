from pathlib import Path

import pytest
from pydantic import ValidationError

from migrate_prove.connectors import RelationRef, SqlAlchemyConnector
from migrate_prove.contract import load_contract, load_suite
from migrate_prove.demo.cte_api import seed_cte_api
from migrate_prove.engine import ValidationEngine
from migrate_prove.models import CheckStatus, ConnectionConfig, Entity
from migrate_prove.salesforce import SalesforceMockConnector, project_salesforce_record
from migrate_prove.secrets import build_connector, describe_connection
from migrate_prove.sql import source_relation


def test_entity_requires_table_xor_sql():
    with pytest.raises(ValidationError):
        Entity.model_validate(
            {
                "name": "x",
                "source_table": "A",
                "source_sql": "SELECT 1 AS id",
                "target_table": "B",
                "business_key": ["id"],
                "mappings": [{"source": "id", "target": "id"}],
            }
        )
    with pytest.raises(ValidationError):
        Entity.model_validate(
            {
                "name": "x",
                "target_table": "B",
                "business_key": ["id"],
                "mappings": [{"source": "id", "target": "id"}],
            }
        )


def test_relation_sql_wraps_cte(tmp_path: Path):
    db = tmp_path / "t.db"
    conn = SqlAlchemyConnector(f"sqlite:///{db.as_posix()}")
    conn.execute("CREATE TABLE t (id INTEGER)")
    conn.execute("INSERT INTO t VALUES (1), (2)")
    ref = RelationRef(sql="WITH x AS (SELECT id FROM t) SELECT id FROM x", name="src")
    assert "AS" in conn.relation_sql(ref)
    assert conn.count_rows(ref) == 2
    info = conn.inspect_relation(ref)
    assert "id" in info.columns


def test_project_strips_attributes():
    row = project_salesforce_record(
        {"attributes": {"type": "Account"}, "Id": "001", "Name": "Ada"}
    )
    assert row == {"Id": "001", "Name": "Ada"}


def test_salesforce_mock_pagination(tmp_path: Path):
    fixtures = tmp_path / "sf"
    (fixtures / "describe").mkdir(parents=True)
    (fixtures / "query").mkdir(parents=True)
    (fixtures / "describe" / "Account.json").write_text(
        '{"name":"Account","fields":[{"name":"Id","type":"id","nillable":false},'
        '{"name":"Name","type":"string","nillable":false}]}',
        encoding="utf-8",
    )
    (fixtures / "query" / "Account.page1.json").write_text(
        '{"done":false,"nextRecordsUrl":"/next","records":['
        '{"attributes":{"type":"Account"},"Id":"1","Name":"A"}]}',
        encoding="utf-8",
    )
    (fixtures / "query" / "Account.page2.json").write_text(
        '{"done":true,"records":[{"attributes":{"type":"Account"},"Id":"2","Name":"B"}]}',
        encoding="utf-8",
    )
    sf = SalesforceMockConnector(fixtures)
    rows = sf.fetch_rows(RelationRef(table="Account"))
    assert [r["Id"] for r in rows] == ["1", "2"]
    assert sf.count_rows(RelationRef(table="Account")) == 2
    assert "Id" in sf.inspect_table("Account").columns


def test_salesforce_mock_connection_config(tmp_path: Path):
    cfg = ConnectionConfig(kind="salesforce_mock", fixtures="fixtures/salesforce")
    assert cfg.kind == "salesforce_mock"
    with pytest.raises(ValidationError):
        ConnectionConfig(kind="salesforce_mock")
    with pytest.raises(ValidationError):
        ConnectionConfig(kind="salesforce_mock", fixtures="x", url="sqlite:///a.db")
    label = describe_connection(cfg, tmp_path)
    assert label.startswith("salesforce_mock://")


def test_cte_api_demo_catches_status_slice(tmp_path: Path):
    seed_cte_api(tmp_path)
    # Copy contract/suite from examples or inline minimal paths
    example = Path(__file__).resolve().parents[1] / "examples" / "cte_api_demo"
    stm = load_contract(example / "stm.yaml")
    suite = load_suite(example / "suite.yaml")
    source = build_connector({"url": f"sqlite:///{(tmp_path / 'source.db').as_posix()}"}, tmp_path, "source")
    target = build_connector(
        {"kind": "salesforce_mock", "fixtures": str(tmp_path / "fixtures" / "salesforce")},
        tmp_path,
        "target",
    )
    assert source_relation(stm.entities[0]).sql is not None
    report = ValidationEngine(source, target).run(stm, levels=suite.levels)
    by_id = {item.id: item for item in report.results}
    assert by_id["L2.accounts.volume.total"].status == CheckStatus.PASS
    assert by_id["L2.accounts.volume.by_Status__c"].status == CheckStatus.FAIL
    assert report.failed > 0
