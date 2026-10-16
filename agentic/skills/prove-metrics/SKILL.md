---
name: prove-metrics
description: >-
  Authors migrate-prove metric checks in suite YAML (sum / group_sum with
  tolerance). Use when finance or KPI tolerances are agreed for an entity.
---

# prove-metrics

## Goal

Reconcile aggregate measures within agreed tolerance.

## Suite fragments

```yaml
checks:
  - id: customers.balance_sum
    kind: sum
    entity: customers
    field: Balance              # source field; target via STM mapping
    tolerance_abs: 0.0001

  - id: customers.balance_by_status
    kind: group_sum
    entity: customers
    field: Balance
    dimensions: [status]
    tolerance_abs: 0.01
```

Escape hatch: `source_expr` / `target_expr` when dialects need raw aggregates.

## Rules

- Do **not** invent tolerances — ask stakeholders or defer the metric.
- Prefer source `field` + STM mapping over dual SQL strings.
- Hop: only if the hop changes money/measures. E2E: financial / KPI recon.

## Concern

**reconciliation**
