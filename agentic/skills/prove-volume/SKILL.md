---
name: prove-volume
description: >-
  Authors migrate-prove volume checks in suite YAML (count / group_count with
  optional filters and side-aware dimensions). Use when defining cardinality
  for hop or e2e suites.
---

# prove-volume

## Goal

Prove totals and **sliced** cardinality after applying STM lookups on source dimensions — not only `COUNT(*)`.

## Suite fragments

```yaml
checks:
  - id: customers.count
    kind: count
    entity: customers
    # filter:
    #   source: "load_type = '${load_type}'"
    #   target: "Load_Type__c = '${load_type}'"

  - id: customers.by_status
    kind: group_count
    entity: customers
    dimensions: [status]   # mapped via STM; or { field, side, source, target }
```

## Guidance

| Scope | Depth |
| --- | --- |
| Hop | Totals + key dimensions the hop owns |
| E2E | Full slices after business lookups (status, region, period, …) |

Batch metadata (`load_date`, …) belongs on the check (`filter` / `side: target`), not the STM.

## Concern

Totals / missing rows → **migration**; bad lookup slices → **transformation**

## Do not

- Rely on matching totals alone as cutover proof
- Put volume_slices on the STM (suite owns checks)
