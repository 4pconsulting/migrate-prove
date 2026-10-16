from pathlib import Path

from migrate_prove.connectors import SqlAlchemyConnector
from migrate_prove.contract import load_contract, load_suite
from migrate_prove.demo.hops_vs_e2e import seed_hops_vs_e2e
from migrate_prove.engine import ValidationEngine
from migrate_prove.models import CheckStatus

SUITES = Path("examples/hops_vs_e2e/suites")
CONTRACTS = Path("examples/hops_vs_e2e/contracts")


def _engine(db: Path) -> ValidationEngine:
    url = f"sqlite:///{db.as_posix()}"
    return ValidationEngine(
        SqlAlchemyConnector(url, "source"),
        SqlAlchemyConnector(url, "target"),
        schema_check=False,
        row_smoke_n=None,
    )


def test_thin_hops_pass_while_e2e_fails_on_status(tmp_path: Path):
    db = seed_hops_vs_e2e(tmp_path)
    engine = _engine(db)

    hop_01_suite = load_suite(SUITES / "hop_01.yaml")
    hop_02_suite = load_suite(SUITES / "hop_02.yaml")
    e2e_suite = load_suite(SUITES / "e2e.yaml")

    hop_01 = engine.run(
        load_contract(CONTRACTS / "hop_01_legacy_to_staging.yaml"),
        checks=hop_01_suite.checks,
    )
    hop_02 = engine.run(
        load_contract(CONTRACTS / "hop_02_staging_to_mart.yaml"),
        checks=hop_02_suite.checks,
    )
    e2e = engine.run(
        load_contract(CONTRACTS / "e2e_legacy_to_mart.yaml"),
        checks=e2e_suite.checks,
    )

    assert hop_01.failed == 0, [r.id for r in hop_01.results if r.status != CheckStatus.PASS]
    assert hop_02.failed == 0, [r.id for r in hop_02.results if r.status != CheckStatus.PASS]

    by_id = {item.id: item for item in e2e.results}
    assert by_id["customers.count"].status == CheckStatus.PASS
    assert by_id["customers.by_status"].status == CheckStatus.FAIL
    assert any(
        item.id.startswith("customers.transforms.") and item.status == CheckStatus.FAIL
        for item in e2e.results
    )
    assert by_id["customers.hash.signature"].status == CheckStatus.FAIL
    assert e2e.failed >= 1
    assert hop_01.failed == 0 and hop_02.failed == 0 and e2e.failed > 0
