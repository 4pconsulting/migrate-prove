"""One-sided prove: emit query results when only one connection is present."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from migrate_prove.connectors import SqlAlchemyConnector
from migrate_prove.engine import ValidationEngine
from migrate_prove.models import CheckStatus, Contract, Entity, SuiteCheck, SuiteConfig


def _entity() -> Entity:
    return Entity.model_validate(
        {
            "name": "accounts",
            "source_table": "Customer",
            "target_table": "Account",
            "business_key": ["External_Id__c"],
            "mappings": [
                {"source": "CustID", "target": "External_Id__c"},
                {"source": "Name", "target": "Name"},
            ],
        }
    )


def _seed(db: Path) -> None:
    conn = SqlAlchemyConnector(f"sqlite:///{db.as_posix()}")
    conn.execute("CREATE TABLE Customer (CustID TEXT, Name TEXT)")
    conn.execute("CREATE TABLE Account (External_Id__c TEXT, Name TEXT)")
    conn.execute("INSERT INTO Customer VALUES ('C1','Ada'), ('C2','Bob')")
    conn.execute("INSERT INTO Account VALUES ('C1','Ada'), ('C2','Bob')")
    conn.engine.dispose()


def test_suite_requires_at_least_one_connection():
    with pytest.raises(ValidationError):
        SuiteConfig.model_validate({"name": "x", "contract": "stm.yaml"})


def test_legacy_run_levels_rejected():
    with pytest.raises(ValidationError):
        SuiteConfig.model_validate(
            {
                "name": "x",
                "contract": "stm.yaml",
                "run": "prove",
                "source": {"url": "sqlite:///a.db"},
                "target": {"url": "sqlite:///b.db"},
            }
        )
    with pytest.raises(ValidationError):
        SuiteConfig.model_validate(
            {
                "name": "x",
                "contract": "stm.yaml",
                "levels": ["1", "2"],
                "source": {"url": "sqlite:///a.db"},
                "target": {"url": "sqlite:///b.db"},
            }
        )


def test_one_sided_target_suite_ok():
    suite = SuiteConfig.model_validate(
        {
            "name": "x",
            "contract": "stm.yaml",
            "target": {"url": "sqlite:///t.db"},
            "checks": [{"id": "a.count", "kind": "count", "entity": "accounts"}],
        }
    )
    assert suite.source is None
    assert suite.target is not None


def test_one_sided_count_emits_pass(tmp_path: Path):
    db = tmp_path / "obs.db"
    _seed(db)
    url = f"sqlite:///{db.as_posix()}"
    target = SqlAlchemyConnector(url, name="target")
    checks = [
        SuiteCheck(id="accounts.count", kind="count", entity="accounts"),
    ]
    report = ValidationEngine(
        None,
        target,
        schema_check=False,
        row_smoke_n=None,
    ).run(Contract(name="obs", entities=[_entity()]), checks=checks)
    by_id = {item.id: item for item in report.results}
    assert by_id["accounts.count"].status == CheckStatus.PASS
    assert by_id["accounts.count"].actual == 2
    assert by_id["accounts.count"].evidence.get("output", {}).get("side") == "target"
    assert "one-sided" in by_id["accounts.count"].message


def test_one_sided_source_emits_pass(tmp_path: Path):
    db = tmp_path / "obs.db"
    _seed(db)
    url = f"sqlite:///{db.as_posix()}"
    source = SqlAlchemyConnector(url, name="source")
    checks = [SuiteCheck(id="accounts.count", kind="count", entity="accounts")]
    report = ValidationEngine(source, None, schema_check=False, row_smoke_n=None).run(
        Contract(name="obs", entities=[_entity()]), checks=checks
    )
    assert report.results[0].status == CheckStatus.PASS
    assert report.results[0].evidence["output"]["side"] == "source"


def test_invariant_skipped_without_target(tmp_path: Path):
    db = tmp_path / "obs.db"
    _seed(db)
    url = f"sqlite:///{db.as_posix()}"
    source = SqlAlchemyConnector(url, name="source")
    checks = [
        SuiteCheck.model_validate(
            {
                "id": "accounts.temporal",
                "kind": "invariant",
                "entity": "accounts",
                "invariant": {
                    "kind": "temporal",
                    "columns": ["External_Id__c", "Name"],
                    "expect": 0,
                },
            }
        )
    ]
    report = ValidationEngine(source, None, schema_check=False, row_smoke_n=None).run(
        Contract(name="obs", entities=[_entity()]), checks=checks
    )
    assert report.results[0].status == CheckStatus.SKIP
