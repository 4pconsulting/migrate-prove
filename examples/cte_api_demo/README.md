# CTE source → Salesforce (mock) target

This demo shows migrate-prove is **not** limited to physical table → physical table comparisons.

- **Source:** a SQLite database with `Customer` and `AccountBalance`. The STM uses a **CTE** (`source_sql`) to join them into one logical grain.
- **Target:** Salesforce `Account` records loaded from **paginated query fixtures** (no live API / network).

Planted defect: `C005` is `Closed` (`C`) in source but lands as `Active` in Salesforce. Totals still match; sliced volume by `Status__c` fails.

See [ADVANCED.md](../../ADVANCED.md) for the architecture.

## Run

```powershell
migrate-prove seed-cte-api-demo examples/cte_api_demo
migrate-prove run examples/cte_api_demo/suite.yaml
```

Expect exit code `1`, with **total volume PASS** and **volume by Status__c FAIL**.

Scorecard: `artifacts/report.html`
