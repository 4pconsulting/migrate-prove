from pathlib import Path

import pytest
from pydantic import ValidationError

from migrate_prove.connectors import SqlAlchemyConnector
from migrate_prove.engine import ValidationEngine
from migrate_prove.models import CheckStatus, Contract, Entity, SuiteConfig


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


def test_prove_requires_both_connections():
    with pytest.raises(ValidationError):
        SuiteConfig.model_validate(
            {
                "name": "x",
                "contract": "stm.yaml",
                "run": "prove",
                "source": {"url": "sqlite:///a.db"},
            }
        )


def test_observe_target_requires_target_connection():
    with pytest.raises(ValidationError):
        SuiteConfig.model_validate(
            {
                "name": "x",
                "contract": "stm.yaml",
                "run": "observe-target",
                "source": {"url": "sqlite:///a.db"},
            }
        )
    suite = SuiteConfig.model_validate(
        {
            "name": "x",
            "contract": "stm.yaml",
            "run": "observe-target",
            "target": {"url": "sqlite:///t.db"},
        }
    )
    assert suite.source is None
    assert suite.target is not None
    assert suite.observe_side == "target"
    assert suite.is_observe


def test_legacy_mode_observe_side_maps_to_run():
    suite = SuiteConfig.model_validate(
        {
            "name": "x",
            "contract": "stm.yaml",
            "mode": "observe",
            "observe_side": "source",
            "source": {"url": "sqlite:///s.db"},
        }
    )
    assert suite.run == "observe-source"


def test_legacy_and_run_together_rejected():
    with pytest.raises(ValidationError):
        SuiteConfig.model_validate(
            {
                "name": "x",
                "contract": "stm.yaml",
                "run": "prove",
                "mode": "prove",
                "source": {"url": "sqlite:///a.db"},
                "target": {"url": "sqlite:///b.db"},
            }
        )


def test_observe_target_exports_and_skips_pairwise(tmp_path: Path):
    db = tmp_path / "obs.db"
    _seed(db)
    url = f"sqlite:///{db.as_posix()}"
    target = SqlAlchemyConnector(url, name="target")
    out = tmp_path / "artifacts"
    engine = ValidationEngine(
        None,
        target,
        run="observe-target",
        output_dir=out,
    )
    report = engine.run(
        Contract(name="obs", entities=[_entity()]),
        levels=["1", "2", "4", "hash", "5"],
    )
    assert report.failed == 0
    assert report.run == "observe-target"
    exports = report.observation_exports()
    assert len(exports) == 1
    path = Path(exports[0]["path"])
    assert path.exists()
    assert exports[0]["row_count"] == 2

    statuses = {item.level: item.status for item in report.results if item.level != "observe"}
    assert statuses["1"] == CheckStatus.SKIP
    assert statuses["2"] == CheckStatus.SKIP
    assert statuses["4"] == CheckStatus.SKIP
    assert statuses["hash"] == CheckStatus.SKIP
    observe = next(item for item in report.results if item.level == "observe")
    assert observe.status == CheckStatus.PASS
    assert observe.evidence.get("sample")
    assert observe.actual["row_count"] == 2
    assert "row_count=2" in observe.message
    assert "sample=" in observe.message


def test_observe_source_skips_invariants(tmp_path: Path):
    db = tmp_path / "obs.db"
    _seed(db)
    url = f"sqlite:///{db.as_posix()}"
    source = SqlAlchemyConnector(url, name="source")
    report = ValidationEngine(
        source,
        None,
        run="observe-source",
        output_dir=tmp_path / "artifacts",
    ).run(Contract(name="obs", entities=[_entity()]), levels=["observe", "5"])
    assert any(
        item.level == "5" and item.status == CheckStatus.SKIP for item in report.results
    )
