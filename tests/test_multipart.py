from pathlib import Path

import pytest
from pydantic import ValidationError

from migrate_prove.connectors import SqlAlchemyConnector
from migrate_prove.engine import ValidationEngine
from migrate_prove.models import CheckStatus, Entity
from migrate_prove.sql import fetch_concat, source_relations, target_relations


def _seed_split_target(db: Path) -> None:
    conn = SqlAlchemyConnector(f"sqlite:///{db.as_posix()}")
    conn.execute(
        "CREATE TABLE Customer (CustID TEXT, Name TEXT, Status TEXT, Balance REAL)"
    )
    conn.execute(
        "CREATE TABLE Account_Personal (External_Id__c TEXT, Name TEXT, Status__c TEXT, Balance__c REAL)"
    )
    conn.execute(
        "CREATE TABLE Account_Business (External_Id__c TEXT, Name TEXT, Status__c TEXT, Balance__c REAL)"
    )
    conn.execute(
        "INSERT INTO Customer VALUES ('C1','Ada','A',10), ('C2','Bob','C',20), ('C3','Cy','A',30)"
    )
    conn.execute(
        "INSERT INTO Account_Personal VALUES ('C1','Ada','Active',10), ('C2','Bob','Closed',20)"
    )
    conn.execute("INSERT INTO Account_Business VALUES ('C3','Cy','Active',30)")
    conn.engine.dispose()


def test_entity_parts_xor():
    with pytest.raises(ValidationError):
        Entity.model_validate(
            {
                "name": "x",
                "source_table": "A",
                "source_parts": [{"table": "B"}],
                "target_table": "T",
                "business_key": ["id"],
                "mappings": [{"source": "id", "target": "id"}],
            }
        )
    with pytest.raises(ValidationError):
        Entity.model_validate(
            {
                "name": "x",
                "source_table": "A",
                "target_table": "T",
                "target_parts": [{"table": "U"}],
                "business_key": ["id"],
                "mappings": [{"source": "id", "target": "id"}],
            }
        )
    entity = Entity.model_validate(
        {
            "name": "accounts",
            "source_table": "Customer",
            "target_parts": [
                {"table": "Account_Personal"},
                {"table": "Account_Business"},
            ],
            "business_key": ["External_Id__c"],
            "mappings": [
                {"source": "CustID", "target": "External_Id__c"},
                {
                    "source": "Status",
                    "target": "Status__c",
                    "lookup": {"A": "Active", "C": "Closed"},
                },
                {"source": "Balance", "target": "Balance__c"},
            ],
            "volume_slices": [["Status__c"]],
        }
    )
    assert len(target_relations(entity)) == 2
    assert len(source_relations(entity)) == 1


def test_multipart_volume_and_slice(tmp_path: Path):
    db = tmp_path / "split.db"
    _seed_split_target(db)
    url = f"sqlite:///{db.as_posix()}"
    source = SqlAlchemyConnector(url, name="source")
    target = SqlAlchemyConnector(url, name="target")
    entity = Entity.model_validate(
        {
            "name": "accounts",
            "source_table": "Customer",
            "target_parts": [
                {"table": "Account_Personal"},
                {"table": "Account_Business"},
            ],
            "business_key": ["External_Id__c"],
            "mappings": [
                {"source": "CustID", "target": "External_Id__c"},
                {"source": "Name", "target": "Name"},
                {
                    "source": "Status",
                    "target": "Status__c",
                    "lookup": {"A": "Active", "C": "Closed"},
                },
                {"source": "Balance", "target": "Balance__c"},
            ],
            "volume_slices": [["Status__c"]],
        }
    )
    from migrate_prove.models import Contract

    contract = Contract(name="split", entities=[entity])
    report = ValidationEngine(source, target).run(contract, levels=["1", "2"])
    by_id = {item.id: item for item in report.results}
    assert by_id["L2.accounts.volume.total"].status == CheckStatus.PASS
    assert by_id["L2.accounts.volume.total"].expected == 3
    assert by_id["L2.accounts.volume.by_Status__c"].status == CheckStatus.PASS

    rows = fetch_concat(target, target_relations(entity))
    assert sorted(row["External_Id__c"] for row in rows) == ["C1", "C2", "C3"]


def test_multipart_schema_fails_when_part_missing_column(tmp_path: Path):
    db = tmp_path / "bad.db"
    conn = SqlAlchemyConnector(f"sqlite:///{db.as_posix()}")
    conn.execute("CREATE TABLE Customer (CustID TEXT, Name TEXT)")
    conn.execute("CREATE TABLE Account_Personal (External_Id__c TEXT, Name TEXT)")
    # Business part missing Name
    conn.execute("CREATE TABLE Account_Business (External_Id__c TEXT)")
    conn.execute("INSERT INTO Customer VALUES ('C1','Ada')")
    conn.execute("INSERT INTO Account_Personal VALUES ('C1','Ada')")
    conn.execute("INSERT INTO Account_Business VALUES ('C2')")
    conn.engine.dispose()

    url = f"sqlite:///{db.as_posix()}"
    entity = Entity.model_validate(
        {
            "name": "accounts",
            "source_table": "Customer",
            "target_parts": [
                {"table": "Account_Personal"},
                {"table": "Account_Business"},
            ],
            "business_key": ["External_Id__c"],
            "mappings": [
                {"source": "CustID", "target": "External_Id__c"},
                {"source": "Name", "target": "Name"},
            ],
        }
    )
    from migrate_prove.models import Contract

    report = ValidationEngine(
        SqlAlchemyConnector(url, name="source"),
        SqlAlchemyConnector(url, name="target"),
    ).run(Contract(name="bad", entities=[entity]), levels=["1"])
    columns = next(item for item in report.results if item.id.endswith(".columns"))
    assert columns.status == CheckStatus.FAIL
    assert "Name" in columns.actual["target_missing"]
