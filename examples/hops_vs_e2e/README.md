# Hops vs end-to-end (runnable)

Thin hop contracts can all pass while a finalised **legacy → mart** STM fails. See [ASSURANCE.md](../../ASSURANCE.md) for the concept.

## Pipeline

```
legacy_customer ──hop_01 (PASS)──► staging_customer ──hop_02 thin (PASS)──► mart_customer
        │                                                                      ▲
        └──────────────── e2e full STM (FAIL: Closed misclassified) ───────────┘
```

Planted defect: `C005` is `C` (Closed) in legacy and staging, but `Active` in mart. Row totals still match.

## Run

```powershell
migrate-prove seed-hops-demo examples/hops_vs_e2e

migrate-prove run examples/hops_vs_e2e/suites/hop_01.yaml
migrate-prove run examples/hops_vs_e2e/suites/hop_02.yaml
migrate-prove run examples/hops_vs_e2e/suites/e2e.yaml
```

Expect hop_01 and hop_02 exit `0`. Expect e2e exit `1` with **total volume PASS** and **volume by status FAIL**.

Scorecards:

- `artifacts/hop_01/report.html`
- `artifacts/hop_02/report.html`
- `artifacts/e2e/report.html`
