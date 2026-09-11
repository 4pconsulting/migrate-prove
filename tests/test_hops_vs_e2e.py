from pathlib import Path

from migrate_prove.connectors import SqlAlchemyConnector
from migrate_prove.contract import load_contract
from migrate_prove.demo.hops_vs_e2e import seed_hops_vs_e2e
from migrate_prove.engine import ValidationEngine
from migrate_prove.models import CheckStatus

EXAMPLES = Path("examples/hops_vs_e2e/contracts")


def _engine(db: Path) -> ValidationEngine:
    url = f"sqlite:///{db.as_posix()}"
    return ValidationEngine(
        SqlAlchemyConnector(url, "source"),
        SqlAlchemyConnector(url, "target"),
    )


def test_thin_hops_pass_while_e2e_fails_on_status(tmp_path: Path):
    db = seed_hops_vs_e2e(tmp_path)
    engine = _engine(db)

    hop_01 = engine.run(load_contract(EXAMPLES / "hop_01_legacy_to_staging.yaml"))
    hop_02 = engine.run(load_contract(EXAMPLES / "hop_02_staging_to_mart.yaml"), levels=["1", "2"])
    e2e = engine.run(load_contract(EXAMPLES / "e2e_legacy_to_mart.yaml"))

    assert hop_01.failed == 0, [r.id for r in hop_01.results if r.status != CheckStatus.PASS]
    assert hop_02.failed == 0, [r.id for r in hop_02.results if r.status != CheckStatus.PASS]

    by_id = {item.id: item for item in e2e.results}
    assert by_id["L2.customers.volume.total"].status == CheckStatus.PASS
    assert by_id["L2.customers.volume.by_status"].status == CheckStatus.FAIL
    assert by_id["L4.customers.transform.status"].status == CheckStatus.FAIL
    assert by_id["HASH.customers.row_signature"].status == CheckStatus.FAIL
    assert e2e.failed >= 1

    # Lesson: hop-only green would have signed off a bad mart.
    assert hop_01.failed == 0 and hop_02.failed == 0 and e2e.failed > 0
