---
name: prove-probes
description: >-
  Configures migrate-prove failure-mode probes as suite checks (truncation,
  precision, leading_zeros, charset, null_empty_literal). Mapping constraints
  (max_length, decimal_places) stay on the STM.
---

# prove-probes

## Goal

Hunt known edge-case defects that totals and schemas miss (hard FAIL).

Auto schema WARN + row smoke already soft-check mapping constraints; use `kind: probes` when you want a gate.

## Probe types

| Probe | Hunts |
| --- | --- |
| `truncation` | Values longer than `max_length` / silently shortened |
| `precision` | Decimals changed vs `normalize.decimal_places` |
| `leading_zeros` | Identifiers that lost leading `0` |
| `charset` | Non-ASCII corrupted in transit |
| `null_empty_literal` | NULL / `""` / `"NULL"` / `"N/A"` vs normalize policy |

## Suite fragment

```yaml
checks:
  - id: customers.probes
    kind: probes
    entity: customers
    probes: [truncation, precision, leading_zeros, charset, null_empty_literal]
```

## When to include

- **Hop (raw → staging):** prefer probes that match the hop’s risk
- **E2E critical entities:** include probes relevant to mapped types
- Skip probes that cannot apply (e.g. no decimals → skip `precision`)
