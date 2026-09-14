from pathlib import Path

from migrate_prove.engine import RunReport
from migrate_prove.models import CheckResult, CheckStatus, Concern, Contract, Entity, Mapping
from migrate_prove.reporting import write_html


def _contract() -> Contract:
    return Contract(
        name="test",
        entities=[
            Entity(
                name="accounts",
                source_table="accounts",
                target_table="Account",
                business_key=["External_Id__c"],
                mappings=[
                    Mapping(source="CustID", target="External_Id__c"),
                ],
            )
        ],
    )


def _report(*results: CheckResult) -> RunReport:
    return RunReport(
        name="cte-salesforce-account-migration",
        started_at="2026-09-14T08:55:37+00:00",
        finished_at="2026-09-14T08:55:38+00:00",
        results=list(results),
    )


def test_pass_row_has_no_details(tmp_path: Path):
    report = _report(
        CheckResult(
            id="L1.accounts.columns",
            level="1",
            title="Mapped columns exist",
            entity="accounts",
            concern=Concern.migration,
            status=CheckStatus.PASS,
            message="All mapped columns are present",
            expected={"source_missing": [], "target_missing": []},
            actual={"source_missing": [], "target_missing": []},
        )
    )
    path = tmp_path / "report.html"
    write_html(report, _contract(), path)
    html = path.read_text(encoding="utf-8")
    assert "<details" not in html
    assert "All mapped columns are present" in html
    assert ">Message<" in html


def test_fail_sample_renders_table(tmp_path: Path):
    report = _report(
        CheckResult(
            id="L4.accounts.transform.Status__c",
            level="4",
            title="Transform Status__c",
            entity="accounts",
            concern=Concern.transformation,
            status=CheckStatus.FAIL,
            expected=0,
            actual=1,
            variance=1,
            message="1 row(s) do not match the STM rule",
            evidence={
                "sample": [
                    {
                        "key": {"External_Id__c": "C005"},
                        "expected": "Closed",
                        "actual": "Active",
                    }
                ],
                "evaluated": 12,
            },
        )
    )
    path = tmp_path / "report.html"
    write_html(report, _contract(), path)
    html = path.read_text(encoding="utf-8")
    assert "<details class=\"result fail-row\">" in html
    assert "L4.accounts.transform.Status__c" in html
    assert "C005" in html
    assert "Closed" in html
    assert "Active" in html
    assert "Rows evaluated:" in html
    assert "Sample mismatches" in html
    assert 'class="evidence-table"' in html


def test_fail_slices_prefer_mismatches(tmp_path: Path):
    report = _report(
        CheckResult(
            id="L2.accounts.volume.by_Status__c",
            level="2",
            title="Volume by Status__c",
            entity="accounts",
            concern=Concern.transformation,
            status=CheckStatus.FAIL,
            expected={"Active": 8, "Closed": 2, "Suspended": 2},
            actual={"Active": 9, "Suspended": 2, "Closed": 1},
            message="Segmented counts differ",
            evidence={
                "slices": [
                    {
                        "slice": {"Status__c": "Suspended"},
                        "source": 2,
                        "target": 2,
                        "variance": 0,
                    },
                    {
                        "slice": {"Status__c": "Active"},
                        "source": 8,
                        "target": 9,
                        "variance": 1,
                    },
                    {
                        "slice": {"Status__c": "Closed"},
                        "source": 2,
                        "target": 1,
                        "variance": -1,
                    },
                ]
            },
        )
    )
    path = tmp_path / "report.html"
    write_html(report, _contract(), path)
    html = path.read_text(encoding="utf-8")
    assert "Status__c=Active" in html
    assert 'class="mismatch"' in html
    # Mismatched slices appear before the matching Suspended slice in the table body.
    active_pos = html.index("Status__c=Active")
    closed_pos = html.index("Status__c=Closed")
    suspended_pos = html.index("Status__c=Suspended")
    assert active_pos < suspended_pos
    assert closed_pos < suspended_pos
    assert "Expected" in html
    assert "Actual" in html
