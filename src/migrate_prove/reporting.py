from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from migrate_prove.engine import LEVEL_TITLES, RunReport
from migrate_prove.models import CheckResult, CheckStatus, Contract

_SLICE_DISPLAY_CAP = 50
_EXPANDABLE = {CheckStatus.FAIL, CheckStatus.ERROR, CheckStatus.WARN}

_STATUS_TONE = {
    CheckStatus.PASS: "#0f7b4c",
    CheckStatus.FAIL: "#b42318",
    CheckStatus.ERROR: "#b42318",
    CheckStatus.WARN: "#b54708",
    CheckStatus.SKIP: "#667085",
}


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
    rows = [_render_result_row(item) for item in report.results]
    body_rows = "\n".join(rows)
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <title>migrate-prove — {_esc(report.name)}</title>
  <style>
    body {{ font-family: "Segoe UI", sans-serif; margin: 32px; color: #101828; background: #f8fafc; }}
    h1 {{ margin-bottom: 0; }}
    .muted {{ color: #667085; }}
    .cards {{ display: flex; gap: 16px; margin: 24px 0; flex-wrap: wrap; }}
    .card {{ background: white; border: 1px solid #eaecf0; border-radius: 12px; padding: 16px 20px; min-width: 140px; }}
    .card strong {{ font-size: 28px; display: block; }}
    .banner {{ padding: 16px 20px; border-radius: 12px; margin: 16px 0; }}
    .fail {{ background: #fee4e2; }}
    .pass {{ background: #d1fadf; }}
    .scorecard {{ background: white; border: 1px solid #eaecf0; border-radius: 12px; overflow: hidden; }}
    .scorecard-head, .row-summary, .row-plain {{
      display: grid;
      grid-template-columns: 4.5rem minmax(6rem, 1fr) minmax(8rem, 1.6fr) 5rem 7.5rem minmax(10rem, 2fr);
      gap: 8px 12px;
      align-items: center;
      padding: 8px 12px;
      font-size: 14px;
    }}
    .scorecard-head {{ background: #f2f4f7; font-weight: 600; border-bottom: 1px solid #eaecf0; }}
    .row-plain, details.result {{ border-bottom: 1px solid #eaecf0; }}
    details.result:last-child, .row-plain:last-child {{ border-bottom: none; }}
    details.result > summary.row-summary {{
      list-style: none;
      cursor: pointer;
      position: relative;
      padding-left: 28px;
    }}
    details.result > summary.row-summary::-webkit-details-marker {{ display: none; }}
    details.result > summary.row-summary::before {{
      content: "▸";
      color: #667085;
      position: absolute;
      left: 10px;
      top: 50%;
      transform: translateY(-50%);
    }}
    details.result[open] > summary.row-summary::before {{ content: "▾"; }}
    details.result[open] {{ background: #fffbfa; }}
    details.result.fail-row[open], details.result.error-row[open] {{
      border-left: 3px solid #b42318;
    }}
    details.result.warn-row[open] {{ border-left: 3px solid #b54708; }}
    .panel {{
      padding: 12px 16px 16px 28px;
      border-top: 1px solid #f2f4f7;
      font-size: 13px;
    }}
    .panel h3 {{ margin: 12px 0 6px; font-size: 13px; color: #344054; }}
    .panel h3:first-child {{ margin-top: 0; }}
    .meta-id {{ font-family: ui-monospace, Consolas, monospace; font-size: 12px; color: #475467; }}
    .compare {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
      gap: 10px;
      margin: 8px 0 4px;
    }}
    .compare .cell {{
      background: #f9fafb;
      border: 1px solid #eaecf0;
      border-radius: 8px;
      padding: 8px 10px;
    }}
    .compare .label {{ display: block; font-size: 11px; color: #667085; text-transform: uppercase; letter-spacing: 0.03em; margin-bottom: 4px; }}
    .evidence-table {{ width: 100%; border-collapse: collapse; margin-top: 4px; background: white; }}
    .evidence-table th, .evidence-table td {{
      text-align: left; padding: 6px 8px; border: 1px solid #eaecf0; font-size: 12px; vertical-align: top;
    }}
    .evidence-table th {{ background: #f2f4f7; }}
    .evidence-table tr.mismatch {{ background: #fef3f2; }}
    .kv {{ margin: 0; }}
    .kv dt {{ font-weight: 600; color: #475467; }}
    .kv dd {{ margin: 0 0 6px 0; word-break: break-word; }}
    pre.sql {{
      background: #f2f4f7; padding: 8px 10px; border-radius: 8px; overflow-x: auto;
      font-size: 12px; margin: 4px 0 0;
    }}
    @media (max-width: 900px) {{
      .scorecard-head {{ display: none; }}
      .row-summary, .row-plain {{
        grid-template-columns: 1fr 1fr;
        gap: 4px 8px;
      }}
    }}
  </style>
</head>
<body>
  <h1>Migration evidence scorecard</h1>
  <p class="muted">{_esc(report.name)} · {_esc(report.started_at)} → {_esc(report.finished_at)}</p>
  <div class="banner {'fail' if report.failed else 'pass'}">
    {"Cutover is not proven: critical or contract checks failed." if (critical or report.failed) else "All executed checks passed. Target matches the migration contract within this run."}
    {" Critical entity failures: " + _esc(", ".join(critical)) if critical else ""}
  </div>
  <div class="cards">
    <div class="card"><span class="muted">Checks</span><strong>{report.total}</strong></div>
    <div class="card"><span class="muted">Passed</span><strong>{report.passed}</strong></div>
    <div class="card"><span class="muted">Failed</span><strong>{report.failed}</strong></div>
  </div>
  <h2>Results</h2>
  <div class="scorecard">
    <div class="scorecard-head">
      <span>Layer</span><span>Entity</span><span>Check</span><span>Status</span><span>Concern</span><span>Message</span>
    </div>
    {body_rows}
  </div>
  <p class="muted">A green ETL exit code is not this report. This report is the oracle: target = STM(source). Expand failed rows for expected/actual values and evidence samples.</p>
</body>
</html>
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")


def _render_result_row(item: CheckResult) -> str:
    tone = _STATUS_TONE[item.status]
    cells = (
        f"<span>{_esc(item.level)}</span>"
        f"<span>{_esc(item.entity)}</span>"
        f"<span>{_esc(item.title)}</span>"
        f"<span style='color:{tone};font-weight:600'>{item.status.value}</span>"
        f"<span>{_esc(item.concern.value)}</span>"
        f"<span>{_esc(item.message)}</span>"
    )
    expandable = item.status in _EXPANDABLE and _has_detail(item)
    if not expandable:
        return f'<div class="row-plain">{cells}</div>'
    status_class = f"{item.status.value.lower()}-row"
    panel = _render_detail_panel(item)
    return (
        f'<details class="result {status_class}">'
        f'<summary class="row-summary">{cells}</summary>'
        f"{panel}"
        f"</details>"
    )


def _has_detail(item: CheckResult) -> bool:
    if item.expected is not None or item.actual is not None or item.variance is not None:
        return True
    evidence = item.evidence or {}
    if evidence.get("sample"):
        return True
    if evidence.get("slices"):
        return True
    if evidence.get("sql"):
        return True
    if evidence.get("fields"):
        return True
    if evidence.get("columns"):
        return True
    if evidence.get("mode") is not None:
        return True
    if evidence.get("evaluated") is not None:
        return True
    return False


def _render_detail_panel(item: CheckResult) -> str:
    parts = [f'<div class="panel">', f'<div class="meta-id">{_esc(item.id)}</div>']
    compare = _render_compare(item)
    if compare:
        parts.append(compare)

    evidence = jsonable(item.evidence or {})
    if not isinstance(evidence, dict):
        evidence = {}

    sample = evidence.get("sample")
    if isinstance(sample, list) and sample:
        parts.append("<h3>Sample mismatches</h3>")
        parts.append(_render_sample_table(sample))

    slices = evidence.get("slices")
    if isinstance(slices, list) and slices:
        parts.append("<h3>Slices</h3>")
        parts.append(_render_slices_table(slices))

    if evidence.get("fields"):
        fields = evidence["fields"]
        parts.append("<h3>Hashed fields</h3>")
        parts.append(f"<p>{_esc(', '.join(str(f) for f in fields))}</p>")

    if evidence.get("columns"):
        cols = evidence["columns"]
        parts.append("<h3>Columns</h3>")
        parts.append(f"<p>{_esc(', '.join(str(c) for c in cols))}</p>")

    if evidence.get("mode") is not None:
        parts.append(f"<p><span class='muted'>Mode:</span> {_esc(str(evidence['mode']))}</p>")

    if evidence.get("evaluated") is not None:
        parts.append(
            f"<p><span class='muted'>Rows evaluated:</span> {_esc(str(evidence['evaluated']))}</p>"
        )

    if evidence.get("sql"):
        parts.append("<h3>SQL</h3>")
        parts.append(f"<pre class='sql'>{_esc(str(evidence['sql']))}</pre>")

    parts.append("</div>")
    return "".join(parts)


def _render_compare(item: CheckResult) -> str:
    cells = []
    for label, value in (
        ("Expected", item.expected),
        ("Actual", item.actual),
        ("Variance", item.variance),
    ):
        if value is None:
            continue
        cells.append(
            f'<div class="cell"><span class="label">{label}</span>{_fmt_value(value)}</div>'
        )
    if not cells:
        return ""
    return f'<div class="compare">{"".join(cells)}</div>'


def _fmt_value(value: Any) -> str:
    value = jsonable(value)
    if isinstance(value, dict):
        if not value:
            return "—"
        items = "".join(
            f"<dt>{_esc(str(k))}</dt><dd>{_esc(str(v))}</dd>" for k, v in value.items()
        )
        return f'<dl class="kv">{items}</dl>'
    if isinstance(value, list):
        if not value:
            return "—"
        if all(not isinstance(v, (dict, list)) for v in value):
            return _esc(", ".join(str(v) for v in value))
        return f"<pre class='sql'>{_esc(json.dumps(value, indent=2))}</pre>"
    return _esc(str(value))


def _render_sample_table(sample: list[Any]) -> str:
    rows = [jsonable(row) for row in sample if isinstance(row, dict)]
    if not rows:
        return "<p class='muted'>No sample rows.</p>"

    columns: list[str] = []
    seen: set[str] = set()

    def add_col(name: str) -> None:
        if name not in seen:
            seen.add(name)
            columns.append(name)

    for row in rows:
        key = row.get("key")
        if isinstance(key, dict):
            for k in key:
                add_col(str(k))
        for k, v in row.items():
            if k == "key":
                continue
            if isinstance(v, dict):
                for nested in v:
                    add_col(f"{k}.{nested}")
            else:
                add_col(str(k))

    header = "".join(f"<th>{_esc(col)}</th>" for col in columns)
    body_parts = []
    for row in rows:
        cells = []
        key = row.get("key") if isinstance(row.get("key"), dict) else {}
        for col in columns:
            if isinstance(key, dict) and col in key:
                cells.append(f"<td>{_esc(str(key[col]))}</td>")
                continue
            if col in row and not isinstance(row[col], dict):
                cells.append(f"<td>{_esc(str(row[col]))}</td>")
                continue
            if "." in col:
                prefix, _, rest = col.partition(".")
                nested = row.get(prefix)
                if isinstance(nested, dict) and rest in nested:
                    cells.append(f"<td>{_esc(str(nested[rest]))}</td>")
                    continue
            cells.append("<td>—</td>")
        body_parts.append(f"<tr>{''.join(cells)}</tr>")

    return (
        f'<table class="evidence-table"><thead><tr>{header}</tr></thead>'
        f"<tbody>{''.join(body_parts)}</tbody></table>"
    )


def _render_slices_table(slices: list[Any]) -> str:
    prepared: list[dict[str, Any]] = []
    for raw in slices:
        item = jsonable(raw)
        if not isinstance(item, dict):
            continue
        variance = item.get("variance")
        mismatched = variance not in (0, 0.0, None)
        prepared.append({**item, "_mismatch": mismatched})

    prepared.sort(key=lambda row: (not row["_mismatch"], _slice_sort_key(row.get("slice"))))
    total = len(prepared)
    shown = prepared[:_SLICE_DISPLAY_CAP]
    note = ""
    if total > _SLICE_DISPLAY_CAP:
        note = (
            f"<p class='muted'>Showing {_SLICE_DISPLAY_CAP} of {total} slices "
            f"(mismatches first).</p>"
        )

    body_parts = []
    for row in shown:
        slice_val = row.get("slice")
        if isinstance(slice_val, dict):
            slice_text = ", ".join(f"{k}={v}" for k, v in slice_val.items())
        else:
            slice_text = "" if slice_val is None else str(slice_val)
        cls = ' class="mismatch"' if row["_mismatch"] else ""
        body_parts.append(
            f"<tr{cls}>"
            f"<td>{_esc(slice_text)}</td>"
            f"<td>{_esc(str(row.get('source', '—')))}</td>"
            f"<td>{_esc(str(row.get('target', '—')))}</td>"
            f"<td>{_esc(str(row.get('variance', '—')))}</td>"
            f"</tr>"
        )

    table = (
        '<table class="evidence-table"><thead>'
        "<tr><th>Slice</th><th>Source</th><th>Target</th><th>Variance</th></tr>"
        f"</thead><tbody>{''.join(body_parts)}</tbody></table>"
    )
    return note + table


def _slice_sort_key(slice_val: Any) -> str:
    if isinstance(slice_val, dict):
        return "|".join(f"{k}={v}" for k, v in sorted(slice_val.items(), key=lambda kv: str(kv[0])))
    return "" if slice_val is None else str(slice_val)


def _esc(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
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
