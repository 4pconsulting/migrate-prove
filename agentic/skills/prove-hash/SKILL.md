---
name: prove-hash
description: >-
  Prepares migrate-prove row hashing in STM YAML (business_key, source_key,
  in_hash, normalize policies, sample strategy). Use when enabling HASH-level
  missing/extra key and row signature checks.
---

# prove-hash

## Goal

Make entities hashable: stable business keys and normalize policies so SHA-256 row signatures are comparable after STM replay.

## STM fields that matter

```yaml
business_key: [customer_id]     # target column names
source_key: [CustID]            # optional if BK mapping is non-obvious
mappings:
  - source: CustID
    target: customer_id
    kind: direct
    in_hash: true               # set false to exclude from signature
    normalize: { trim: true, upper: true }
sample:
  strategy: full                # or stratified (policy on entity; pushdown TBD)
  # strata:
  #   - name: recent
  #     where: "updated_at >= '2024-01-01'"
  #     rate: 0.1
```

## Guidance

| Scope | Depth |
| --- | --- |
| Hop | Sampled or key-only on high volume |
| E2E critical | Prefer full (or risk-stratified) |

Missing/extra keys → **migration**. Value signature mismatch → **transformation**.

## Rules

- Every `business_key` target must map from a source (or set `source_key`).
- Align `normalize` with how ETL canonicalises — hash is unforgiving.
- Surrogate PKs: hash/join on business keys, not warehouse surrogates.

## Do not

- Assume SQL-pushdown hashing (v1 is in-process)
- Exclude critical mapped fields from `in_hash` without reason
