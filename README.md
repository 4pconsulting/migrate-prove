# migrate-prove

A Python validation suite for data migrations. It does **not** treat a green ETL exit code as success. It proves that the **target dataset is the result of applying an agreed Source-to-Target Mapping (STM) to the source**, then that the target is still internally coherent.

This repository is the executable version of a five-layer migration test strategy: contract-as-oracle, risk-calibrated checks, row hashing, failure-mode probes, and an evidence scorecard.

## Why this exists

A completed pipeline answers: *did the job finish?*

This suite answers: **can we systematically prove the target represents the source correctly, according to the contract and business invariants?**

`COUNT(*)` matching on both sides is not that proof. The banking demo plants a status misclassification that keeps totals identical and still fails sliced volume, hashing, and invariants.

## Architecture

```
STM YAML (oracle)
        │
        ▼
┌─────────────────────────────────────┐
│  ValidationEngine                   │
│  1 Schema  2 Volume  3 Metrics     │
│  4 Transforms  HASH  5 Invariants   │
│  + failure-mode probes             │
└─────────────────────────────────────┘
        │
        ▼
 JSON + HTML evidence scorecard
 tagged: migration | transformation | reconciliation
```

### The contract is the test oracle

Target correctness is **not** `source == target`. Different can be right (status `A` → `Active`). The STM file is the shared, versioned specification:

| Dimension | How the suite tests it |
| --- | --- |
| Direct projections | Canonicalise, then compare / hash |
| Transformations | Lookup tables and `when`/`then` rules run against source, asserted on target |
| Type coercions | Decimal scale, string length, identifier quoting |
| Null / empty / `N/A` | Failure probe `null_empty_literal` |
| Dedup / filters | Optional `filter_source` / `filter_target` |
| Cardinality | Total counts **and** slices (`volume_slices`) |
| Surrogate keys | Join on mapped business keys, not physical PKs |

### Five layers (+ hash + probes)

| Layer | Question | Typical concern tag |
| --- | --- | --- |
| L1 Schema | Do mapped objects/columns exist? | migration |
| Probes | Truncation, precision, encoding, leading zeros | transformation |
| L2 Volume | Totals and slices after applying STM lookups | migration / transformation |
| L3 Metrics | Sums and grouped financials within tolerance | reconciliation |
| L4 Transforms | Every STM rule vs actual target value | transformation |
| HASH | Missing/extra keys vs row SHA-256 | migration / transformation |
| L5 Invariants | Temporal order, orphans, state machines | reconciliation |

Separating those tags is how you debug cutover night: dropped rows vs bad lookup vs legacy junk copied faithfully.

### Risk calibration

Each entity has `risk: critical | master | high_volume`. Critical failures are called out on the HTML scorecard. Sampling policy (`full` or `stratified` with `where` + `rate`) is on the entity so high-volume logs are not brute-forced during a four-hour window. v1 hashing still runs in-process (SQLite/demo scale); SQL-pushdown hashing is the next connector increment.

## Quick start

Ensure you are in the root `migrate-prove` directory.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
python -m pip install -e ".[dev]"
migrate-prove seed-demo examples/banking_demo
migrate-prove compile examples/banking_demo/stm.yaml
migrate-prove run examples/banking_demo/suite.yaml
```

Open `examples/banking_demo/artifacts/report.html`. You should see a **passing total count** and failing sliced volume, transforms, hashes, probes, and invariants — that is the point.

```powershell
pytest
```

## Authoring a contract

See `examples/banking_demo/stm.yaml`. A suite file points at the contract and the two connection URLs:

```yaml
name: my-cutover
contract: stm.yaml
source: { url: sqlite:///source.db }   # any SQLAlchemy URL
target: { url: postgresql+psycopg://... }
output_dir: artifacts
```

Transform rules are the oracle, not a second copy of ETL Python. Keep ETL and this file aligned; do not reimplement warehouse SQL ad hoc in tests.

## Roadmap (v1 is the skeleton that already fails the right way)

1. **Now** — STM YAML, SQLAlchemy connectors, L1–L5, hashing, probes, CLI, HTML/JSON, SQLite demo.
2. **Excel STM import** — optional extra `openpyxl` to ingest the spreadsheet everyone still has.
3. **Dialect hash pushdown** — `SHA2` / `sha256` in Postgres, SQL Server, Snowflake so row signatures never leave the estate.
4. **Stratified sampling at warehouse scale** — deterministic modulus on business keys, 100% for HNW / recent / edge strata.
5. **JUnit XML + CI gate** — cutover sign-off as a pipeline artifact.
6. **Rehearsal profiles** — synthetic → prod-replica → dress-rehearsal, with runtime budgets.

## Design rules

- The STM is executable. If it cannot be checked, it is not specified.
- Prefer evidence over exit codes.
- Hunt known failure modes on purpose; do not only sample happy-path rows.
- Never claim success from a global row count.
