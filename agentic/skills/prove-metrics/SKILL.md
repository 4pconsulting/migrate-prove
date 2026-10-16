---
name: prove-metrics
description: >-
  Authors migrate-prove L3 metric reconciliation in STM YAML (SUM/COUNT with
  absolute or percent tolerance and optional group_by). Use when finance or KPI
  tolerances are agreed for an entity.
---

# prove-metrics (L3)

## Goal

Reconcile aggregate measures within agreed tolerance.

## STM fragment

```yaml
metrics:
  - name: total_balance
    source_sql: SUM(Balance)
    target_sql: SUM(current_balance)
    tolerance_abs: 0.0001
  - name: balance_by_status
    source_sql: SUM(Balance)
    target_sql: SUM(current_balance)
    group_by: [status]
    tolerance_abs: 0.01
  - name: account_count
    source_sql: COUNT(*)
    target_sql: COUNT(*)
    # tolerance_pct: 0.1   # optional percent
```

## Rules

- Do **not** invent tolerances — ask stakeholders or defer the metric.
- `source_sql` / `target_sql` are aggregate expressions (engine wraps them).
- `group_by` uses **target** column names after STM mapping for grouping logic.
- Hop: only if the hop changes money/measures. E2E: financial / KPI recon.

## Concern

**reconciliation**

## Defer

If tolerances are unknown, leave `metrics: []` and exclude `"3"` from suite `levels` until finance signs off.
