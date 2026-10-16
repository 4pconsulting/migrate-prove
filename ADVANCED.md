# Advanced usage: CTE sources, multi-part relations, observe mode, and API targets

migrate-prove’s oracle is always the same:

> **target ≈ agreed transformation of source**

A physical table on both sides is the *common* case, not the *definition*. This document covers advanced relation shapes that keep that oracle intact:

1. **CTE / multi-table SQL sources** (`source_sql`)
2. **Multi-part concat** (`source_parts` / `target_parts`) when one grain lands in several physical objects
3. **Observe mode** — one-sided export when the other side is unreachable
4. **API targets** such as Salesforce (JSON + pagination), via the connector seam

Runnable demo: [`examples/cte_api_demo`](examples/cte_api_demo).

---

## Logical relations, not just tables

An entity describes a **row grain** and field mappings. How those rows are materialised is the connector’s job.

```
┌──────────────────────────────┐          ┌──────────────────────────────┐
│ source_table | source_sql    │          │ target_table | target_sql    │
│   OR source_parts (concat)   │ ──STM──► │   OR target_parts (concat)   │
└──────────────────────────────┘          └──────────────────────────────┘
```

Internally, checks use one or more `RelationRef` values (table **or** inline SQL). Multi-part sides are **concatenated in-process** into one logical row set, so volume slices, metrics, hash, and transforms can still group by any mapped dimension.

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

- Provide **exactly one** of `source_table` | `source_sql` | `source_parts`.
- Provide **exactly one** of `target_table` | `target_sql` | `target_parts`.
- Project **aliases the mappings refer to** (`CustID`, `Balance`, …). The engine does not know about base tables inside the CTE.
- Watch **grain**: joins can inflate or collapse rows. Volume, hash keys, and metrics must match the intended business key after the CTE.
- `filter_source` remains a SQL `WHERE` fragment applied **outside** the CTE wrapper.
- L1 schema for CTE sources is inferred from `SELECT * FROM (source_sql) … LIMIT 0` (column names), not from a physical table catalog.

Alternative without `source_sql`: materialise a view/staging table and point `source_table` at it (or prove the join as a hop — see [ASSURANCE.md](ASSURANCE.md)).

---

## Multi-part concat (`source_parts` / `target_parts`)

Use when one STM grain is split across physical objects (e.g. personal vs business accounts) and you need a single comparable row set:

```yaml
entities:
  - name: accounts
    source_table: Customer
    target_parts:
      - table: Account_Personal
      - table: Account_Business
    business_key: [External_Id__c]
    volume_slices:
      - [Status__c]
    mappings:
      - source: CustID
        target: External_Id__c
      - source: Status
        target: Status__c
        lookup: { A: Active, C: Closed }
```

Rules:

- Each part is `{ table: … }` or `{ sql: … }` (optional `schema`).
- All parts must expose the **mapped / key / slice columns**; L1 fails if any part is missing a required column.
- Counts and grouped aggregates **sum across parts**; row-level checks see the concatenated set.
- L5 `orphans` / raw `sql` require a **single** target relation — use `target_sql` with a SQL `UNION ALL` when you need joins on SQLAlchemy.

---

## Observe mode (one-sided export)

When only source *or* only target is reachable, run an observation for manual reconciliation:

```yaml
name: accounts-observe-target
contract: stm.yaml
mode: observe
observe_side: target
target:
  url: ${TARGET_URL}
# source omitted
output_dir: artifacts
levels: ["1", "2", "4", "hash"]
```

Or force for one run: `migrate-prove run suite.yaml --observe target`.

Behaviour:

1. Builds only the live connector
2. Writes `artifacts/observe/{entity}.{side}.ndjson` plus a `.meta.json` sidecar (row count, columns, filter)
3. Pairwise checks (L1–L4, hash) are **SKIP** with a clear message
4. L5 runs only when `observe_side=target` and the entity has a single target relation

Exit code 0 when export succeeds and nothing ERRORs/FAILs (SKIP alone is success).

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

### Mock connector (CI-safe)

`SalesforceMockConnector` implements the same `Connector` surface as the live client:

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

### Live Salesforce (`kind: salesforce`)

Requires `pip install migrate-prove[salesforce]` (pulls `httpx`). Put the four vars in `.env` using stubs from [`.env.example`](.env.example).

```yaml
target:
  kind: salesforce
  url: ${SALESFORCE_URL}                    # login / My Domain root
  api_version: ${SALESFORCE_API_VERSION}    # e.g. 59.0
  client_id: ${SALESFORCE_CLIENT_ID}
  client_secret: ${SALESFORCE_CLIENT_SECRET}
  # buffer_rows: 5000   # optional; spill flush size
  # spill_dir: artifacts/sf-spill
  # max_rows: 100000    # optional hard stop
```

Flow:

1. `POST {url}/services/oauth2/token` with `grant_type=client_credentials`
2. Use `access_token` + token response `instance_url` for Describe / Query
3. **Row fetches** (`fetch_rows` — L4 / HASH / observe): follow `nextRecordsUrl` until `done`; rows spill to NDJSON on disk (default buffer **5000**)
4. **Aggregates** (L2 volume / L3 metrics): SOQL pushdown — `COUNT()`, `COUNT(Id) … GROUP BY`, `SUM(field)` — without downloading every row. Falls back to in-process only if the expression/dimensions are not pushable (e.g. non-field `dimension_sql`)

API version ≥ 51 uses `SELECT FIELDS(ALL)` for row fetches; older versions select described field names (capped).

### Declarative HTTP (`kind: http`)

Requires `pip install migrate-prove[http]`. Env var names belong in [`.env.example`](.env.example).

```yaml
target:
  kind: http
  base_url: ${API_BASE_URL}
  auth:
    type: bearer          # bearer | header | basic
    token: ${API_TOKEN}
  resources:
    Account:              # must match entity target_table (or part table name)
      list_path: /v1/accounts
      schema_path: /v1/accounts/schema   # optional Salesforce-like {fields:[…]}
      records_jmespath: data
      next_jmespath: next
  # buffer_rows: 5000
  # max_rows: 100000
```

Auth variants:

```yaml
# API key header
auth:
  type: header
  header_name: X-Api-Key
  header_value: ${API_KEY}

# HTTP basic
auth:
  type: basic
  username: ${API_USER}
  password: ${API_PASSWORD}
```

`dialect_name()` is `http`; checks treat it like Salesforce for L5 SKIP paths. Rows are flat scalars only (nested objects/lists dropped). Pagination uses JMESPath for the record list and next-page URL.

### Keys and filters

- Prefer **External ID** (or another stable business key) as `business_key`. Do not use Salesforce `Id` as the migration key unless that is truly the STM.
- `filter_target` is dialect-shaped: SOQL-ish predicates on Salesforce, SQL `WHERE` on warehouses, same simple AND equality (applied in-process) on HTTP.

### When to prove live vs landed extract

| Approach | Use when |
| --- | --- |
| **Landed extract** (Bulk/CDC → warehouse, then SQL↔SQL) | Cutover evidence, full hash, heavy metrics |
| **Live / mock API connector** | Hop gates, smoke after load, External ID presence, sampled transforms |

Full L3–L5 against a live high-volume org is usually the wrong cost model for a four-hour window. Prefer `max_rows` / filters on API connectors; entity `sample` wiring into L4/HASH is still a roadmap item.

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
      ├── SqlAlchemyConnector     ← tables or (source_sql) AS _rel
      ├── SalesforceMockConnector ← fixture describe + paginated JSON
      ├── SalesforceConnector     ← OAuth + live query pages → disk spill
      └── HttpConnector           ← declarative REST + JMESPath → disk spill
      │
      ▼
Evidence scorecard (migration | transformation | reconciliation)
```

The five layers, concern tags, and hop vs end-to-end story do not change. Only the **relation providers** do.
