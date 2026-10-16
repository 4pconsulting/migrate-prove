---
name: prove-probes
description: >-
  Configures migrate-prove failure-mode probes on STM entities (truncation,
  precision, leading_zeros, charset, null_empty_literal). Use when authoring
  edge-case checks for first landings or critical entities.
---

# prove-probes

## Goal

Hunt known edge-case defects that totals and schemas miss.

## Probe types

| Probe | Hunts |
| --- | --- |
| `truncation` | Values longer than `max_length` / silently shortened |
| `precision` | Decimals changed vs `normalize.decimal_places` |
| `leading_zeros` | Identifiers that lost leading `0` |
| `charset` | Non-ASCII corrupted in transit |
| `null_empty_literal` | NULL / `""` / `"NULL"` / `"N/A"` vs normalize policy |

## STM fragment

```yaml
failure_probes:
  - truncation
  - precision
  - leading_zeros
  - charset
  - null_empty_literal
```

## When to include

- **Hop (raw → staging):** prefer all probes that match the hop’s risk
- **E2E critical entities:** include probes relevant to mapped types
- Skip probes that cannot apply (e.g. no decimals → skip `precision`)

## Concern

**transformation**

## Do not

- Enable every probe blindly on high_volume entities without risk discussion
- Confuse probes with L4 transform equality — probes are edge-mode hunts
