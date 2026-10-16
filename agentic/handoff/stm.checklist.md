# STM checklist (Oracle → Suite gate)

Artifact: versioned `stm.yaml` (contract / mapping oracle).

## Hard gate

- [ ] `migrate-prove compile <stm.yaml>` exits 0
- [ ] `version` and `name` set
- [ ] Every entity has exactly one of `source_table` | `source_sql` | `source_parts`
- [ ] Every entity has exactly one of `target_table` | `target_sql` | `target_parts`
- [ ] Every entity has `business_key` and at least one `mapping`
- [ ] No secrets in the file
- [ ] No prove-intent fields on entities (`volume_slices`, `metrics`, `invariants`, `failure_probes` belong in the suite)

## Mapping coverage (mark done or deferred for Suite checks)

For each entity, record which suite checks the Suite agent should author: **done** | **deferred (reason)** | **n/a**

| Dimension | Suite check kind / skill | Status |
| --- | --- | --- |
| Schema / structure | auto schema + optional `kind: schema` / prove-schema | |
| Failure probes | `kind: probes` / prove-probes | |
| Volume / slices | `count` / `group_count` / prove-volume | |
| Metrics | `sum` / `group_sum` / prove-metrics | |
| Transforms / mappings | STM mappings + `kind: transforms` / prove-transforms | |
| Hash readiness | `kind: hash` / prove-hash | |
| Invariants | `kind: invariant` / prove-invariants | |

## Risk and scope

- [ ] Each entity has `risk: critical | master | high_volume`
- [ ] Hop vs e2e intent documented (filename or comment in dossier)
- [ ] Deferred checks listed for Suite (`checks[]` will omit them until ready)

## Suite may proceed when

Compile passes **and** transforms (mappings) + business keys are enough for a meaningful first run. Deferred metrics/invariants are allowed if the suite simply omits those checks.
