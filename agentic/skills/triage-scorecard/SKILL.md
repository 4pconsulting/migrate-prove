---
name: triage-scorecard
description: >-
  Interprets migrate-prove JSON/HTML scorecards by concern tag (migration,
  transformation, reconciliation) and recommends STM vs ETL vs data fixes. Use
  after run-prove when analysing failures.
---

# triage-scorecard

## Goal

Turn `report.json` / `report.html` into actionable remediation by **concern**.

## Concern → likely owner

| Concern | Typical cause | First lever |
| --- | --- | --- |
| migration | Dropped/extra rows, schema gaps, load incompleteness | ETL extract/load, filters, keys |
| transformation | Bad lookup, wrong rule, normalize/hash drift | STM correctness **or** transform code |
| reconciliation | Orphans, temporal violations, metric imbalance | Target data quality / downstream rules |

## Read order

1. Summary counts by level and status
2. Critical-risk entity failures first
3. Group by concern, then by entity
4. For HASH: separate missing/extra keys (migration) from signature mismatches (transformation)
5. For volume: total mismatch vs slice-only mismatch (slice-only often transformation)

## Remediation rules

- Propose STM changes as a **diff**; hand to Oracle — do not silent-edit.
- If hops pass and e2e fails, treat as possible composition drift / compensating errors — see `ASSURANCE.md`.
- Passing `COUNT(*)` with failing slices/hash is expected in demos that plant status misclassification.

## Output shape

```markdown
## Scorecard triage
- Suite:
- Exit code:
- Critical failures:
- By concern:
  - migration: …
  - transformation: …
  - reconciliation: …
- Recommended next hops: (Oracle | ETL | data | Suite levels)
```
