# STM checklist (Oracle → Suite gate)

Artifact: versioned `stm.yaml` (contract / oracle).

## Hard gate

- [ ] `migrate-prove compile <stm.yaml>` exits 0
- [ ] `version` and `name` set
- [ ] Every entity has exactly one of `source_table` or `source_sql`
- [ ] Every entity has `target_table`, `business_key`, and at least one `mapping`
- [ ] No secrets in the file

## Dimension coverage (mark done or deferred)

For each entity, record status: **done** | **deferred (reason)** | **n/a**

| Dimension | Skill | Status |
| --- | --- | --- |
| Schema / structure | prove-schema | |
| Failure probes | prove-probes | |
| Volume / slices | prove-volume | |
| Metrics | prove-metrics | |
| Transforms / mappings | prove-transforms | |
| Hash readiness | prove-hash | |
| Invariants | prove-invariants | |

## Risk and scope

- [ ] Each entity has `risk: critical | master | high_volume`
- [ ] Hop vs e2e intent documented (filename or comment in dossier)
- [ ] Deferred levels listed for Suite (`levels` will omit them until ready)

## Suite may proceed when

Compile passes **and** transforms (mappings) + business keys are enough for a meaningful first run. Deferred metrics/invariants are allowed if Suite `levels` exclude them.
