import json
from pathlib import Path

from migrate_prove.connectors import SqlAlchemyConnector
from migrate_prove.contract import load_contract, load_suite
from migrate_prove.demo.banking import seed_banking
from migrate_prove.engine import ValidationEngine
from migrate_prove.models import CheckStatus
from migrate_prove.reporting import write_json


def test_demo_surfaces_masked_volume_and_invariants(tmp_path: Path):
    seed_banking(tmp_path)
    suite = load_suite(Path("examples/banking_demo/suite.yaml"))
    contract = load_contract(Path("examples/banking_demo/stm.yaml"))
    engine = ValidationEngine(
        SqlAlchemyConnector(f"sqlite:///{(tmp_path / 'source.db').as_posix()}", "source"),
        SqlAlchemyConnector(f"sqlite:///{(tmp_path / 'target.db').as_posix()}", "target"),
        schema_check=True,
        row_smoke_n=5,
        params=suite.params,
    )
    report = engine.run(contract, checks=suite.checks)
    by_id = {item.id: item for item in report.results}

    assert by_id["customers.count"].status == CheckStatus.PASS
    assert by_id["customers.count"].evidence.get("output")
    assert by_id["customers.by_status"].status == CheckStatus.FAIL
    assert by_id["customers.by_status"].evidence.get("sample")
    assert by_id["customers.temporal"].status == CheckStatus.FAIL
    assert by_id["order_items.orphans"].status == CheckStatus.FAIL
    assert any(
        item.id.startswith("customers.transforms.") and item.status == CheckStatus.FAIL
        for item in report.results
    )
    assert by_id["customers.probes.truncation"].status == CheckStatus.FAIL
    assert by_id["customers.probes.leading_zeros"].status == CheckStatus.FAIL
    assert by_id["customers.probes.charset"].status == CheckStatus.FAIL
    assert by_id["customers.hash.signature"].status == CheckStatus.FAIL
    assert report.failed >= 6
    assert report.critical_failed(contract)
    assert any(item.id.startswith("auto.schema.") for item in report.results)

    report_path = tmp_path / "report.json"
    write_json(report, report_path)
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["summary"]["total"] == report.total
    by_status = next(item for item in payload["results"] if item["id"] == "customers.by_status")
    assert "Active" in by_status["expected"]
    assert "Active" in by_status["actual"]
