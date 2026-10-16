# migrate-prove — agent facts

## Product

migrate-prove proves that **target ≈ STM(source)** plus target-side invariants. A green ETL exit code is not success. The STM YAML is the **test oracle**.

## CLI

```text
migrate-prove compile <stm.yaml>
migrate-prove run <suite.yaml> [-e entity] [--env-file .env]
migrate-prove seed-demo | seed-hops-demo | seed-cte-api-demo <dir>
```

Exit codes: `0` all pass; `1` FAIL/ERROR; `2` missing env secrets.

## Layers (engine order)

`1` schema → `probes` → `2` volume → `3` metrics → `4` transforms → `hash` → `5` invariants

## Concern tags

| Tag | Meaning |
| --- | --- |
| migration | Transport / completeness (schema, dropped/extra keys, totals) |
| transformation | Logic vs STM (lookups, hashes, probes) |
| reconciliation | Target internal coherence (metrics, invariants) |

## Contract rules

- Do not invent STM fields outside `src/migrate_prove/models.py`
- Secrets never in YAML — only `${ENV}` / `${ENV:-default}`
- Exactly one of `source_table` | `source_sql` | `source_parts` (and same for target_*)
- Suite `mode`: `prove` (default, both connections) or `observe` + `observe_side`
- Connection `kind` today: `sqlalchemy` | `salesforce_mock` | `salesforce` | `http`
- Hop contracts stay thin; e2e STM carries the cutover claim (see `ASSURANCE.md`)

## Agentic pipeline

Orchestrator sequences: Collator → dossier → Oracle → `stm.yaml` → Suite → `suite.yaml` → Prover → scorecard.

Specialists return **QUESTIONS** / **BLOCKERS** when stuck; only the Orchestrator (or human) advances stages. Flat delegation only.

Portable prompts and skills: `agentic/`. Cursor wrappers: `.cursor/`. Operator guide: `AGENTIC.md`.
