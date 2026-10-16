---
name: prove-volume
description: >-
  Authors migrate-prove L2 volume checks in STM YAML (volume_slices after STM
  lookups). Use when defining cardinality and sliced counts for hop or e2e
  contracts.
---

# prove-volume (L2)

## Goal

Prove totals and **sliced** cardinality after applying STM lookups on source dimensions — not only `COUNT(*)`.

## STM fragment

```yaml
volume_slices:
  - [status]
  - [status, region]   # multi-column slice
```

Engine always compares total counts; slices add grouped comparisons. Lookups on mapped fields are applied when slicing source.

## Guidance

| Scope | Depth |
| --- | --- |
| Hop | Totals + key dimensions the hop owns |
| E2E | Full slices after business lookups (status, region, period, …) |

## Concern

Totals / missing rows → **migration**; bad lookup slices → **transformation**

## Do not

- Rely on matching totals alone as cutover proof
- Invent slice dimensions not present in mappings or docs
