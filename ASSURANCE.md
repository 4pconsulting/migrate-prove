# Contract assurance: hops vs end-to-end source → target

Migration and modern ETL (Airflow DAGs, Glue jobs, Lambdas, dbt models) often look like a chain of successful tasks. A green exit code proves that a job finished. It does **not** prove that the data is correct.

**migrate-prove** is built for **contract-based assurance**: an explicit Source-to-Target Mapping (STM) acts as the test oracle. Correctness means:

> **target ≈ agreed transformation of source**  
> (plus target-side business invariants)

That is different from unit-testing a transform function, and different from warehouse-only data quality (e.g. Great Expectations on a single table). It is **paired, evidence-based assurance** for a source → target claim.

---

## Two kinds of contract

### End-to-end (source → final target)

The cutover / business claim:

> Does the **final** dataset represent the **authoritative source** (legacy system of record, or agreed raw feed) according to the STM?

This is what stakeholders need before switching off a legacy system or trusting a migrated mart.

### Hop (adjacent layer → next layer)

The engineering claim:

> Did **this** job preserve or transform data as *this* boundary’s contract requires?

Useful after each Glue/Lambda/dbt step so failures localise quickly.

```
Source ──ETL₁──► Layer1 ──ETL₂──► Layer2 ──ETL₃──► Target (mart)
   │                │                │                    │
   │           hop contract     hop contract              │
   └────────────── end-to-end STM / hash / invariants ────┘
```

**Both are valuable. Hop-only is not enough for cutover assurance.**

Runnable illustration: [examples/hops_vs_e2e](examples/hops_vs_e2e) — thin hops exit 0; the e2e suite fails on sliced status volume while totals still match.

---

## What to test at each level

Align checks with the five-layer model used by migrate-prove. Depth should follow **risk**, not dogma.

| Layer | Question | Typical on a **hop** | Typical on **end-to-end** |
| --- | --- | --- | --- |
| L1 Schema | Do required objects/columns exist? | Yes — after every materialisation | Yes — final schemas vs STM |
| Failure probes | Truncation, precision, encoding, leading zeros, NULL vs `""` | Yes — especially first landings (raw → staging) | Yes — on critical entities |
| L2 Volume | Totals **and** slices (status, region, period) | Light: totals + key dimensions the hop owns | Full: slices after STM lookups |
| L3 Metrics | Sums / averages within tolerance | Only if the hop changes money or measures | Yes — financial / KPI recon |
| L4 Transforms | STM rules vs actual values | Only rules **introduced on this hop** | Full business mapping (lookups, derived fields) |
| Row hash | Missing/extra keys + deterministic signatures | Sampled or key-only on high volume | 100% (or risk-stratified) on critical entities |
| L5 Invariants | Temporal order, orphans, balance, state machines | Rarely — only if the hop creates those relationships | Yes — “is the estate usable?” |

Tag failures by **concern** so root cause is clear:

| Concern | Meaning |
| --- | --- |
| **migration** | Transport / completeness (dropped rows, extra keys, schema) |
| **transformation** | Logic vs STM (bad lookup, wrong derivation) |
| **reconciliation** | Internal coherence (orphans, ledger imbalance, impossible dates) |

---

## How to implement with hops **and** a complete source → target contract

### 1. Finalise the end-to-end STM first

Write (and version) one contract that answers the business question for each critical entity: field mappings, lookups, filters, cardinality, tolerances, invariants, risk level.

Example entity intent (conceptual):

```yaml
# contracts/e2e/customers.yaml  (oracle for cutover)
entities:
  - name: customers
    source_table: legacy.Customer          # system of record
    target_table: mart.dim_customer        # final consumer
    business_key: [customer_id]
    risk: critical
    mappings:
      - source: Status
        target: status
        kind: transform
        lookup: { A: Active, S: Suspended, C: Closed }
      # ...
    volume_slices: [[status]]
    metrics:
      - name: total_balance
        source_sql: SUM(Balance)
        target_sql: SUM(current_balance)
    invariants:
      - name: temporal_integrity
        kind: temporal
        columns: [created_at, updated_at]
```

This file is the **shared specification**. ETL may implement it across three jobs; tests must not invent a second, drifting “truth.”

### 2. Add thin hop contracts at boundaries

Each hop suite points at **adjacent** tables and only the rules that hop owns.

```text
suites/
  hop_01_raw_to_staging.yaml      → contract: hops/raw_to_staging.yaml
  hop_02_staging_to_conformed.yaml
  hop_03_conformed_to_mart.yaml
  e2e_legacy_to_mart.yaml         → contract: e2e/customers.yaml (+ …)
```

Example hop (staging must not map status yet — only land safely):

```yaml
# contracts/hops/raw_to_staging.yaml
entities:
  - name: customer_raw
    source_table: raw.customer
    target_table: staging.customer
    business_key: [cust_id]
    risk: master
    failure_probes: [truncation, charset, leading_zeros, precision]
    mappings:
      - source: CustID
        target: cust_id
        kind: direct
      - source: Status
        target: status_code          # still A/S/C
        kind: direct
    volume_slices: [[status_code]]
```

Example hop that **owns** the status lookup:

```yaml
# contracts/hops/staging_to_conformed.yaml
entities:
  - name: customer_conformed
    source_table: staging.customer
    target_table: conformed.customer
    business_key: [customer_id]
    mappings:
      - source: status_code
        target: status
        kind: transform
        lookup: { A: Active, S: Suspended, C: Closed }
```

The **same** lookup appears in the end-to-end STM (legacy `Status` → mart `status`). Hop 2 implements it; e2e **asserts the business outcome** from legacy through to mart.

### 3. Wire into orchestration

After each job (Airflow / Glue workflow / Step Functions):

1. Run the **hop** suite for that boundary (fail the DAG on FAIL for blocking entities).
2. On schedule or at rehearsal/cutover, run the **e2e** suite against prod-like connections (secrets via env / `.env`, never in YAML).

```text
[Glue: raw→staging] → migrate-prove run hop_01…
[Glue: staging→conformed] → migrate-prove run hop_02…
[Glue: conformed→mart] → migrate-prove run hop_03…
[Cutover gate] → migrate-prove run e2e_legacy_to_mart…
                 → HTML/JSON evidence scorecard for sign-off
```

### 4. Risk-calibrate depth

| Data class | Hop depth | End-to-end depth |
| --- | --- | --- |
| Critical financial / PII | Schema, probes, keys; sample or full hash if cheap | Full STM, metrics, 100% or stratified hash, invariants |
| Master / reference | Schema, volume slices, attribute checks | Full attribute + volume |
| High-volume logs / telemetry | Partition volume + aggregates | Partition recon + stratified sample — not full row scan in the cutover window |

---

## Concrete example: what a scorecard should show

**Scenario:** Legacy has 1,000,000 customers. Mart also has 1,000,000. Hop 1–3 all passed totals.

End-to-end volume **by status** (after applying the STM lookup on source):

| Status   | Source (mapped) | Mart   | Variance |
| -------- | --------------- | ------ | -------- |
| Active   | 900,000         | 950,000 | +50,000 |
| Closed   | 100,000         | 50,000  | −50,000 |
| **Total**| **1,000,000**   | **1,000,000** | **0** |

- Global `COUNT(*)` on every hop: **PASS** (masked error).
- End-to-end sliced volume + L4 status transform: **FAIL**.
- Concern: **transformation** (misclassification), not “rows failed to move.”

Without the e2e contract, hop suites that only check `COUNT(*)` and “column exists” would never surface this.

Another e2e-only catch: mart `updated_at < created_at`, or orphan `order_items`. Hops that never join parent/child will not see it; **L5 on the final target** will.

---

## What having **only** hop contracts means

| You can claim | You cannot honestly claim |
| --- | --- |
| Each job behaved as its local contract specified | The final mart represents the legacy source per business STM |
| Engineering can localise many pipeline defects | Cutover / audit-grade proof of source → target correctness |
| Layer owners have actionable gates | A single oracle shared by risk, QA, and DE |

**Failure modes hop-only misses or blurs:**

1. **Composition drift** — Hop A maps `A`→`Active`; hop C later remaps or collapses statuses. Each hop “passes”; end state is wrong vs legacy.
2. **Lost or double-applied rules** — Soft-delete filter applied twice, or never; local volumes still look plausible.
3. **Compensating errors** — Drop 50 rows in hop 2, invent 50 in hop 3; adjacent totals pass; e2e hash / key recon fails.
4. **No stakeholder oracle** — Sign-off becomes “all DAG tasks green,” which is process success, not data proof.
5. **Ownership blur** — When the mart is wrong, there is no one contract that defines *correct relative to source*.

**Rule of thumb:** hop contracts are **necessary for isolation**, **insufficient for assurance**. Finalise and automate at least one **source → final target** suite for every critical entity before cutover.

---

## Recommended minimum set

For a serious migration or layered warehouse build:

1. **One versioned e2e STM** per critical entity (legacy/SoR → mart or serving table).
2. **Hop contracts** at each materialised boundary (thinner; probes + volume + rules that hop owns).
3. **Evidence artifacts** (JSON/HTML scorecards) retained per rehearsal and cutover.
4. **Secrets in env** (suite YAML only references `${VAR}`) for real Postgres/Athena/etc. connections.
5. Explicit statement in runbooks: *pipeline green ≠ migration proven; e2e scorecard is the gate.*

---

## Relation to other testing styles

| Style | Role |
| --- | --- |
| Unit tests of Glue/Lambda/SQL | Protect transform *code* with fixtures |
| Orchestration sensors / exit codes | Prove jobs *ran* |
| Great Expectations / dbt tests | Ongoing quality on *a* table (often target-only) |
| **Hop contracts** | Prove each *boundary* |
| **End-to-end STM assurance** | Prove the *business* source → target claim |

Use them together. Do not substitute hop-only checks for a finalised source → target contract when the question is: **can we trust the target as a faithful, usable representation of the source?**
