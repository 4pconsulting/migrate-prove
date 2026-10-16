from pathlib import Path

from migrate_prove.connectors import SqlAlchemyConnector
from migrate_prove.engine import ValidationEngine
from migrate_prove.models import CheckStatus, Contract, Entity, SuiteCheck


def _seed(db: Path) -> None:
    conn = SqlAlchemyConnector(f"sqlite:///{db.as_posix()}")
    conn.execute(
        "CREATE TABLE Customer (CustID TEXT, Name TEXT, Status TEXT, Balance REAL, load_type TEXT)"
    )
    conn.execute(
        "CREATE TABLE customers ("
        "customer_id TEXT, customer_name TEXT, status TEXT, current_balance REAL, load_date TEXT)"
    )
    conn.execute(
        "INSERT INTO Customer VALUES ('C1','Ada','A',10,'FULL'), ('C2','Bob','C',20,'FULL')"
    )
    conn.execute(
        "INSERT INTO customers VALUES "
        "('C1','Ada','Active',10,'2026-10-01'), ('C2','Bob','Closed',20,'2026-10-01')"
    )
    conn.engine.dispose()


def _entity() -> Entity:
    return Entity.model_validate(
        {
            "name": "customers",
            "source_table": "Customer",
            "target_table": "customers",
            "business_key": ["customer_id"],
            "mappings": [
                {"source": "CustID", "target": "customer_id", "nullable": False},
                {"source": "Name", "target": "customer_name", "max_length": 40},
                {
                    "source": "Status",
                    "target": "status",
                    "lookup": {"A": "Active", "C": "Closed"},
                },
                {"source": "Balance", "target": "current_balance"},
            ],
        }
    )


def test_auto_schema_warns_without_failing(tmp_path: Path):
    db = tmp_path / "t.db"
    conn = SqlAlchemyConnector(f"sqlite:///{db.as_posix()}")
    conn.execute("CREATE TABLE Customer (CustID TEXT, Name TEXT)")
    # Target missing Name mapping column customer_name
    conn.execute("CREATE TABLE customers (customer_id TEXT)")
    conn.execute("INSERT INTO Customer VALUES ('C1','Ada')")
    conn.execute("INSERT INTO customers VALUES ('C1')")
    conn.engine.dispose()
    url = f"sqlite:///{db.as_posix()}"
    report = ValidationEngine(
        SqlAlchemyConnector(url, "source"),
        SqlAlchemyConnector(url, "target"),
        schema_check=True,
        row_smoke_n=None,
    ).run(Contract(name="t", entities=[_entity()]), checks=[])
    columns = next(i for i in report.results if i.id.endswith(".columns"))
    assert columns.status == CheckStatus.WARN
    assert report.failed == 0
    assert report.warned >= 1


def test_no_schema_check_skips_preamble(tmp_path: Path):
    _seed(tmp_path / "t.db")
    url = f"sqlite:///{(tmp_path / 't.db').as_posix()}"
    report = ValidationEngine(
        SqlAlchemyConnector(url, "source"),
        SqlAlchemyConnector(url, "target"),
        schema_check=False,
        row_smoke_n=None,
    ).run(
        Contract(name="t", entities=[_entity()]),
        checks=[SuiteCheck(id="customers.count", kind="count", entity="customers")],
    )
    assert not any(i.id.startswith("auto.") for i in report.results)
    assert report.results[0].id == "customers.count"


def test_hard_schema_fails(tmp_path: Path):
    db = tmp_path / "t.db"
    conn = SqlAlchemyConnector(f"sqlite:///{db.as_posix()}")
    conn.execute("CREATE TABLE Customer (CustID TEXT)")
    conn.execute("CREATE TABLE customers (customer_id TEXT)")
    conn.engine.dispose()
    url = f"sqlite:///{db.as_posix()}"
    report = ValidationEngine(
        SqlAlchemyConnector(url, "source"),
        SqlAlchemyConnector(url, "target"),
        schema_check=False,
        row_smoke_n=None,
    ).run(
        Contract(name="t", entities=[_entity()]),
        checks=[SuiteCheck(id="customers.schema", kind="schema", entity="customers")],
    )
    columns = next(i for i in report.results if i.id.endswith(".columns"))
    assert columns.status == CheckStatus.FAIL


def test_filter_and_target_only_dimension(tmp_path: Path):
    _seed(tmp_path / "t.db")
    url = f"sqlite:///{(tmp_path / 't.db').as_posix()}"
    checks = [
        SuiteCheck.model_validate(
            {
                "id": "customers.by_load",
                "kind": "group_count",
                "entity": "customers",
                "dimensions": [
                    "status",
                    {"field": "load_date", "side": "target"},
                ],
                "filter": {
                    "source": "load_type = 'FULL'",
                    "target": "load_date = '2026-10-01'",
                },
            }
        )
    ]
    report = ValidationEngine(
        SqlAlchemyConnector(url, "source"),
        SqlAlchemyConnector(url, "target"),
        schema_check=False,
        row_smoke_n=None,
    ).run(Contract(name="t", entities=[_entity()]), checks=checks)
    item = report.results[0]
    assert item.status == CheckStatus.PASS
    assert item.evidence["output"]["filter"]["source"] == "load_type = 'FULL'"
    assert any(d.get("side") == "target" for d in item.evidence["output"]["dimensions"])
