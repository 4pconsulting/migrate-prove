from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from migrate_prove.engine import LEVEL_TITLES, RunReport
from migrate_prove.models import CheckStatus, Contract


def jsonable(value: Any) -> Any:
    """Make nested results safe for json.dumps (tuple dict keys, Decimals, etc.)."""
    if isinstance(value, dict):
        return {_json_key(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _json_key(key: Any) -> str:
    if isinstance(key, str):
        return key
    if isinstance(key, tuple):
        return "|".join("" if part is None else str(part) for part in key)
    return str(key)


def write_json(report: RunReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(jsonable(report.to_dict()), indent=2), encoding="utf-8")


def write_html(report: RunReport, contract: Contract, path: Path) -> None:
    critical = report.critical_failed(contract)
    rows = []
    for item in report.results:
        tone = {
            CheckStatus.PASS: "#0f7b4c",
            CheckStatus.FAIL: "#b42318",
            CheckStatus.ERROR: "#b42318",
            CheckStatus.WARN: "#b54708",
            CheckStatus.SKIP: "#667085",
        }[item.status]
        rows.append(
            f"<tr><td>{item.level}</td><td>{item.entity}</td><td>{item.title}</td>"
            f"<td style='color:{tone};font-weight:600'>{item.status.value}</td>"
            f"<td>{item.concern.value}</td><td>{_esc(item.message)}</td></tr>"
        )
    body_rows = "\n".join(rows)
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <title>migrate-prove — {report.name}</title>
  <style>
    body {{ font-family: "Segoe UI", sans-serif; margin: 32px; color: #101828; background: #f8fafc; }}
    h1 {{ margin-bottom: 0; }}
    .muted {{ color: #667085; }}
    .cards {{ display: flex; gap: 16px; margin: 24px 0; }}
    .card {{ background: white; border: 1px solid #eaecf0; border-radius: 12px; padding: 16px 20px; min-width: 140px; }}
    .card strong {{ font-size: 28px; display: block; }}
    table {{ width: 100%; border-collapse: collapse; background: white; }}
    th, td {{ text-align: left; padding: 8px 10px; border-bottom: 1px solid #eaecf0; font-size: 14px; }}
    th {{ background: #f2f4f7; }}
    .banner {{ padding: 16px 20px; border-radius: 12px; margin: 16px 0; }}
    .fail {{ background: #fee4e2; }}
    .pass {{ background: #d1fadf; }}
  </style>
</head>
<body>
  <h1>Migration evidence scorecard</h1>
  <p class="muted">{report.name} · {report.started_at} → {report.finished_at}</p>
  <div class="banner {'fail' if report.failed else 'pass'}">
    {"Cutover is not proven: critical or contract checks failed." if (critical or report.failed) else "All executed checks passed. Target matches the migration contract within this run."}
    {" Critical entity failures: " + ", ".join(critical) if critical else ""}
  </div>
  <div class="cards">
    <div class="card"><span class="muted">Checks</span><strong>{report.total}</strong></div>
    <div class="card"><span class="muted">Passed</span><strong>{report.passed}</strong></div>
    <div class="card"><span class="muted">Failed</span><strong>{report.failed}</strong></div>
  </div>
  <h2>Results</h2>
  <table>
    <thead><tr><th>Layer</th><th>Entity</th><th>Check</th><th>Status</th><th>Concern</th><th>Evidence</th></tr></thead>
    <tbody>{body_rows}</tbody>
  </table>
  <p class="muted">A green ETL exit code is not this report. This report is the oracle: target = STM(source).</p>
</body>
</html>
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")


def _esc(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def print_console(report: RunReport) -> None:
    from rich.console import Console
    from rich.table import Table

    console = Console()
    table = Table(title=f"migrate-prove · {report.name}")
    table.add_column("Layer")
    table.add_column("Entity")
    table.add_column("Check")
    table.add_column("Status")
    table.add_column("Concern")
    table.add_column("Message")
    for item in report.results:
        style = "green" if item.status == CheckStatus.PASS else "red"
        if item.status == CheckStatus.WARN:
            style = "yellow"
        table.add_row(
            LEVEL_TITLES.get(item.level, item.level),
            item.entity,
            item.title,
            f"[{style}]{item.status.value}[/]",
            item.concern.value,
            item.message,
        )
    console.print(table)
    console.print(
        f"[bold]{report.passed}/{report.total} passed[/] · {report.failed} failed"
    )
