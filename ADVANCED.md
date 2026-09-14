# Advanced usage: CTE sources and API targets

migrate-prove’s oracle is always the same:

> **target ≈ agreed transformation of source**

A physical table on both sides is the *common* case, not the *definition*. This document covers two advanced relation shapes that keep that oracle intact:

1. **CTE / multi-table SQL sources** (`source_sql`)
2. **API targets** such as Salesforce (JSON + pagination), via the connector seam

Runnable demo: [`examples/cte_api_demo`](examples/cte_api_demo).

---

## Logical relations, not just tables

An entity describes a **row grain** and field mappings. How those rows are materialised is the connector’s job.

```
┌──────────────────────────┐          ┌──────────────────────────┐
│ source_table             │          │ target_table             │
│   OR source_sql (CTE)    │ ──STM──► │   OR API object + fetch  │
└──────────────────────────┘          └──────────────────────────┘
```

Internally, checks use a `RelationRef` (table **or** inline SQL) and record-oriented connector methods (`fetch_rows`, `count_rows`, `group_count`, `aggregate`). SQLAlchemy connectors wrap CTEs as subqueries; API connectors project JSON into the same flat `dict` rows the hash / transform layers already expect.

---

## CTE / joined sources (`source_sql`)

Use when the STM grain is not a single physical table:

```yaml
entities:
  - name: accounts
    source_sql: |
      WITH cust AS (
        SELECT CustID, Name, Status, CreatedAt, UpdatedAt FROM Customer
      ),
      bal AS (
        SELECT CustID, Balance FROM AccountBalance
      )
      SELECT c.CustID, c.Name, c.Status, b.Balance, c.CreatedAt, c.UpdatedAt
      FROM cust c
      INNER JOIN bal b ON b.CustID = c.CustID
    target_table: Account
    business_key: [External_Id__c]
    source_key: [CustID]
    mappings:
      - source: CustID
        target: External_Id__c
        # ...
```

Rules:

- Provide **exactly one** of `source_table` or `source_sql`.
- Project **aliases the mappings refer to** (`CustID`, `Balance`, …). The engine does not know about base tables inside the CTE.
- Watch **grain**: joins can inflate or collapse rows. Volume, hash keys, and metrics must match the intended business key after the CTE.
- `filter_source` remains a SQL `WHERE` fragment applied **outside** the CTE wrapper.
- L1 schema for CTE sources is inferred from `SELECT * FROM (source_sql) … LIMIT 0` (column names), not from a physical table catalog.

Alternative without `source_sql`: materialise a view/staging table and point `source_table` at it (or prove the join as a hop — see [ASSURANCE.md](ASSURANCE.md)).

---

## API targets (Salesforce-shaped)

SQLAlchemy URLs are the wrong abstraction for REST/SOQL systems. Suites declare an explicit connection kind:

```yaml
source:
  url: sqlite:///source.db
target:
  kind: salesforce_mock
  fixtures: fixtures/salesforce
```

### Mock connector (this release)

`SalesforceMockConnector` implements the same `Connector` surface a future live Salesforce client would:

| Concern | Behaviour |
| --- | --- |
| Schema (L1) | `describe/{Object}.json` |
| Rows | Paginated `query/{Object}.pageN.json` (`done` / `nextRecordsUrl` / `records`) |
| Projection | Strip `attributes`; flat field → value dicts |
| Aggregates | In-process `COUNT(*)` / `SUM(field)` after fetch |
| Filters | Tiny SOQL-ish `Field = 'value' AND …` only |
| Invariants | `temporal` runs in-process; raw `sql` / `orphans` are skipped |

No network, no OAuth — CI-safe. Fixture layout:

```
fixtures/salesforce/
  describe/Account.json
  query/Account.page1.json
  query/Account.page2.json
```

### Keys and filters

- Prefer **External ID** (or another stable business key) as `business_key`. Do not use Salesforce `Id` as the migration key unless that is truly the STM.
- `filter_target` is dialect-shaped: SOQL-ish predicates on the mock, SQL `WHERE` on warehouses.

### When to prove live vs landed extract

| Approach | Use when |
| --- | --- |
| **Landed extract** (Bulk/CDC → warehouse, then SQL↔SQL) | Cutover evidence, full hash, heavy metrics |
| **Live / mock API connector** | Hop gates, smoke after load, External ID presence, sampled transforms |

Full L3–L5 against a live high-volume org is usually the wrong cost model for a four-hour window. Sampling (`sample` on the entity) and risk tiers still apply.

Live Salesforce HTTP is **out of scope** for this cut; the mock preserves the architectural boundary (`kind: salesforce_mock` today → `kind: salesforce` later).

---

## Demo walkthrough (`examples/cte_api_demo`)

```powershell
migrate-prove seed-cte-api-demo examples/cte_api_demo
migrate-prove run examples/cte_api_demo/suite.yaml
```

What it proves:

1. Source rows come from a **CTE join**, not `Customer` alone.
2. Target rows come from **two Salesforce query pages** merged by the mock connector.
3. Totals still match while **volume by `Status__c` fails** — `C005` is `Closed` in source and `Active` in Salesforce (same teaching point as the banking demo).

Scorecard: `examples/cte_api_demo/artifacts/report.html`.

---

## Mental model

```
STM YAML (oracle)
      │
      ▼
ValidationEngine
      │
      ├── SqlAlchemyConnector  ← tables or (source_sql) AS _rel
      └── SalesforceMockConnector ← describe + paginated JSON → dict rows
      │
      ▼
Evidence scorecard (migration | transformation | reconciliation)
```

The five layers, concern tags, and hop vs end-to-end story do not change. Only the **relation providers** do.
