---
name: run-prove
description: >-
  Runs migrate-prove compile and suite execution, including demo seed commands
  and exit-code interpretation. Use when proving a suite or smoking banking /
  hops / cte_api demos.
---

# run-prove

## Commands

From repo root (editable install assumed):

```powershell
migrate-prove compile path\to\stm.yaml
migrate-prove run path\to\suite.yaml --env-file .env
```

Optional entity filter: `migrate-prove run suite.yaml -e customers`

### Demo seeds

```powershell
migrate-prove seed-demo examples/banking_demo
migrate-prove seed-hops-demo examples/hops_vs_e2e
migrate-prove seed-cte-api-demo examples/cte_api_demo
```

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | All checks PASS (or only WARN/SKIP) |
| 1 | One or more FAIL/ERROR |
| 2 | Missing environment secrets |

## Artifacts

Under suite `output_dir` (default `artifacts`):

- `report.json`
- `report.html`

## Process

1. Compile contract referenced by the suite
2. Ensure env vars from `.env.example` exist when needed
3. Seed if using a demo directory
4. Run suite
5. Hand reports to `triage-scorecard`

## Do not

- Delete failing checks to get exit 0
- Commit `.env` with secrets
