---
name: prove-invariants
description: >-
  Authors migrate-prove L5 business invariants in STM YAML (temporal, orphans,
  or arbitrary target SQL). Use when encoding target-side coherence rules for
  cutover or hop-specific relationships.
---

# prove-invariants (L5)

## Goal

Assert the target estate is usable: temporal order, referential orphans, or custom SQL expectations.

## Kinds

### temporal

```yaml
- name: temporal_integrity
  kind: temporal
  columns: [created_at, updated_at]
  expect: 0
```

### orphans

```yaml
- name: no_orphan_items
  kind: orphans
  child_table: order_items
  child_key: order_id
  parent_table: orders
  parent_key: order_id
  expect: 0
```

### sql

```yaml
- name: no_negative_balances
  kind: sql
  sql: SELECT COUNT(*) FROM customers WHERE current_balance < 0
  expect: 0
  concern: reconciliation   # default
```

## Hop vs e2e

- Hop: rare — only if the hop creates the relationship
- E2E: yes for cutover (“is the estate usable?”)

## Notes

- Invariants run on **target** (and orphans need parent/child on target connector)
- API / Salesforce mock: `temporal` may run in-process; `orphans` / raw `sql` may SKIP

## Concern

Default **reconciliation** (override with `concern` when appropriate)

## Do not

- Encode STM field transforms as invariants — use L4
- Assume source-side SQL in invariant `sql`
