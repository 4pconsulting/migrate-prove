---
name: prove-transforms
description: >-
  Authors migrate-prove L4 field mappings in STM YAML (direct, transform,
  coerce, default, surrogate) including lookups, when/then rules, and
  expressions. Use when implementing the core STM mapping table.
---

# prove-transforms (L4)

## Goal

Encode how each target column is derived from source so the engine can replay STM(source) and compare to target.

## Mapping kinds

| Kind | Use |
| --- | --- |
| `direct` | Projection (+ normalize) |
| `transform` | `lookup`, `rules`, and/or `expression` |
| `coerce` | Type/scale coercion (often with `decimal_places`) |
| `default` | Constant / default when source absent |
| `surrogate` | Target key not from source physical PK — join via business keys |

## Fragments

```yaml
# Lookup
- source: Status
  target: status
  kind: transform
  lookup: { A: Active, S: Suspended, C: Closed }
  lookup_on_missing: fail   # fail | passthrough | null
  # lookup_default: Unknown

# Rules (safe expression AST)
- source: CreditScore
  target: risk_tier
  kind: transform
  rules:
    - when: "credit_score >= 750 and default_history == 0"
      then: "LOW"
    - when: "credit_score >= 600"
      then: "MEDIUM"
    - else: "HIGH"

# Coerce
- source: Balance
  target: current_balance
  kind: coerce
  normalize: { decimal_places: 4 }
```

## Normalize (affects compare + hash)

```yaml
normalize:
  trim: true
  upper: false
  decimal_places: 2
  empty_as_null: true
  null_token: ""
```

## Hop vs e2e

- Hop: only rules **introduced on this hop**
- E2E: full business mapping

## Concern

**transformation**

## Do not

- Reimplement warehouse ETL in unconstrained Python — stay within contract fields
- Put multi-statement SQL in `expression`
