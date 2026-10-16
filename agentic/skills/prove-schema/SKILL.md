---
name: prove-schema
description: >-
  Authors migrate-prove L1 schema structure in STM YAML (source_table or
  source_sql, target_table, schemas, mapping nullability and max_length). Use
  when implementing schema checks or inspecting source/target columns for an
  entity.
---

# prove-schema (L1)

## Goal

Ensure the STM names real objects/columns and captures nullability / length constraints the engine can check.

## When

- Oracle agent deepening an entity
- After Collator provides object names
- When live `inspect_table` / describe metadata is available

## STM fields

```yaml
entities:
  - name: customers
    source_table: Customer          # XOR source_sql
    # source_sql: |
    #   SELECT ...
    target_table: customers
    source_schema: optional
    target_schema: optional
    mappings:
      - source: CustID
        target: customer_id
        nullable: false
        max_length: 40
        type: string   # optional annotation
```

## Rules

- Exactly one of `source_table` or `source_sql`.
- Every mapped `source` / `target` should exist (or be noted as deferred until schema available).
- Prefer `max_length` when docs or DDL state it — feeds probes and L1.
- Hop: verify the hop’s materialised objects. E2E: final schemas vs STM.

## Concern

Failures tag as **migration**.

## Do not

- Invent columns that appear in neither docs nor schema
- Put SQLAlchemy URLs or secrets in the STM
