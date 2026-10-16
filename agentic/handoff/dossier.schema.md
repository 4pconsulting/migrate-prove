# Dossier handoff schema

Artifact path (suggested): `engagements/<name>/dossier.md`

Use [templates/dossier.md](../templates/dossier.md) as the starting shape.

## Required sections (Collator → Oracle gate)

A dossier is ready for the Oracle agent when it has:

1. **Inventory** — input files, types, owners, dates
2. **Entities** — at least one candidate entity with name
3. **Business keys** — candidate key column(s) per entity (may be uncertain)
4. **Mappings** — a table of source → target fields (partial OK)
5. **Conflicts** — explicit list (or “none found”)
6. **Open questions** — what Oracle / humans must resolve
7. **Confidence** — high / medium / low per major claim

## Optional sections

- Lookups and code lists
- Filters / dedup rules
- Known quirks (leading zeros, NULL vs `""`, truncation, charset)
- Metrics / KPI intent (without inventing tolerances)
- Invariants mentioned in docs
- Hop vs e2e scope notes
- Skeleton STM path (if Collator emitted one)

## Rules for Collator

- Extract and normalize; do **not** invent financial tolerances or connection strings
- Prefer quoting source documents over silent reconciliation of conflicts
- Mark gaps instead of filling them with plausible fiction
