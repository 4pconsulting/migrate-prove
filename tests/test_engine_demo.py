import json
from pathlib import Path

from migrate_prove.connectors import SqlAlchemyConnector
from migrate_prove.contract import load_contract
from migrate_prove.demo.banking import seed_banking
from migrate_prove.engine import ValidationEngine
from migrate_prove.models import CheckStatus
from migrate_prove.reporting import write_json


def test_demo_surfaces_masked_volume_and_invariants(tmp_path: Path):
    seed_banking(tmp_path)
    contract = load_contract(Path("examples/banking_demo/stm.yaml"))
    engine = ValidationEngine(
        SqlAlchemyConnector(f"sqlite:///{(tmp_path / 'source.db').as_posix()}", "source"),
        SqlAlchemyConnector(f"sqlite:///{(tmp_path / 'target.db').as_posix()}", "target"),
    )
    report = engine.run(contract)
    by_id = {item.id: item for item in report.results}

    assert by_id["L2.customers.volume.total"].status == CheckStatus.PASS
    assert by_id["L2.customers.volume.by_status"].status == CheckStatus.FAIL
    assert by_id["L5.customers.invariant.temporal_integrity"].status == CheckStatus.FAIL
    assert by_id["L5.order_items.invariant.no_orphan_items"].status == CheckStatus.FAIL
    assert by_id["L4.customers.transform.status"].status == CheckStatus.FAIL
    assert by_id["L4.customers.transform.risk_tier"].status == CheckStatus.FAIL
    assert by_id["PROBE.customers.truncation"].status == CheckStatus.FAIL
    assert by_id["PROBE.customers.leading_zeros"].status == CheckStatus.FAIL
    assert by_id["PROBE.customers.charset"].status == CheckStatus.FAIL
    assert by_id["HASH.customers.row_signature"].status == CheckStatus.FAIL
    assert report.failed >= 6
    assert report.critical_failed(contract)

    report_path = tmp_path / "report.json"
    write_json(report, report_path)
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["summary"]["total"] == report.total
    by_status = next(item for item in payload["results"] if item["id"] == "L2.customers.volume.by_status")
    assert "Active" in by_status["expected"]
    assert "Active" in by_status["actual"]
