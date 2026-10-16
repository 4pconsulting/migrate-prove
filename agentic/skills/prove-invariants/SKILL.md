---
name: prove-invariants
description: >-
  Authors migrate-prove target-side invariants as suite checks (temporal,
  orphans, or arbitrary target SQL). Not the same as mapping max_length /
  nullable (those stay on STM and feed auto schema WARN).
---

# prove-invariants

## Goal

Assert the target estate is usable: temporal order, referential orphans, or custom SQL expectations.

## Suite fragments

### temporal

```yaml
checks:
  - id: customers.temporal
    kind: invariant
    entity: customers
    invariant:
      name: temporal_integrity
      kind: temporal
      columns: [created_at, updated_at]
      expect: 0
```

### orphans

```yaml
checks:
  - id: order_items.orphans
    kind: invariant
    entity: order_items
    invariant:
      name: no_orphan_items
      kind: orphans
      child_table: order_items
      child_key: order_id
      parent_table: orders
      parent_key: order_id
      expect: 0
```

### sql

```yaml
checks:
  - id: customers.custom
    kind: invariant
    entity: customers
    invariant:
      kind: sql
      sql: "SELECT COUNT(*) FROM customers WHERE status IS NULL"
      expect: 0
```

Requires a target connection (SKIP if source-only). Orphans/raw SQL need a single SQL target relation.
