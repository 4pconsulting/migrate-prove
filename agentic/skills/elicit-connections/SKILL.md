---
name: elicit-connections
description: >-
  Interviews operators for migrate-prove source and target connection details
  (Athena, MSSQL, Postgres, SQLite, Salesforce, APIs, Excel, CTE sources) and
  maps answers to Suite ConnectionConfig without embedding secrets. Use when
  authoring suite.yaml.
---

# elicit-connections

## Goal

Produce safe `source` / `target` blocks for `suite.yaml` plus `.env.example` entries.
Prefer copying variable stubs from the repo root [`.env.example`](../../../.env.example).

## Interview (ask per side: source and target)

1. **Role:** source or target? (or observe-only one side)
2. **System kind:** Athena | MSSQL | Postgres | SQLite | other SQL | Salesforce | other API | Excel/files | unknown
3. **Shape:**
   - Table(s) vs CTE / custom SQL (`source_sql` / `target_sql` live on the **entity**, not the connection)
   - Split grain → `source_parts` / `target_parts` (concat) — see ADVANCED.md
   - For Salesforce: mock fixtures path vs live API
   - For other APIs: list/schema paths, pagination JMESPath, auth type (`bearer` | `header` | `basic`)
4. **How to connect:** full SQLAlchemy URL vs structured dialect/host/port/database
5. **Reachability:** both sides (`mode: prove`) or one-sided (`mode: observe` + `observe_side`)
6. **Secrets:** env var names for user/password/token (never paste production secrets into chat logs if avoidable — prefer var names)
7. **Extras:** query options, workgroup, SSL, catalog (`options`); for API connectors optional `buffer_rows` / `spill_dir` / `max_rows`

## Map to supported config

### SQLAlchemy (default `kind: sqlalchemy`)

```yaml
source:
  url: ${SOURCE_URL}
```

or:

```yaml
source:
  dialect: postgresql+psycopg   # or mssql+pyodbc, awsathena+rest, sqlite, …
  host: ${SOURCE_HOST}
  port: 5432
  database: ${SOURCE_DB}
  username: ${SOURCE_USER}
  password: ${SOURCE_PASSWORD}
  options: {}
```

### Salesforce mock (no secrets)

```yaml
target:
  kind: salesforce_mock
  fixtures: fixtures/salesforce
```

### Live Salesforce (`pip install migrate-prove[salesforce]`)

```yaml
target:
  kind: salesforce
  url: ${SALESFORCE_URL}
  api_version: ${SALESFORCE_API_VERSION}
  client_id: ${SALESFORCE_CLIENT_ID}
  client_secret: ${SALESFORCE_CLIENT_SECRET}
```

OAuth2 client-credentials: token from `{SALESFORCE_URL}/services/oauth2/token`, then Bearer auth against the token response `instance_url`. Query pages spill to disk (default buffer 5000 rows).

`.env.example` stubs: `SALESFORCE_URL`, `SALESFORCE_API_VERSION`, `SALESFORCE_CLIENT_ID`, `SALESFORCE_CLIENT_SECRET`.

### Declarative HTTP (`pip install migrate-prove[http]`)

```yaml
target:
  kind: http
  base_url: ${API_BASE_URL}
  auth:
    type: bearer                 # bearer | header | basic
    token: ${API_TOKEN}
    # header_name: X-Api-Key
    # header_value: ${API_KEY}
    # username: ${API_USER}
    # password: ${API_PASSWORD}
  resources:
    Account:
      list_path: /v1/accounts
      schema_path: /v1/accounts/schema   # optional
      records_jmespath: data
      next_jmespath: next
```

`.env.example` stubs: `API_BASE_URL`, `API_TOKEN`, and/or `API_KEY` / `API_USER` / `API_PASSWORD`.

### Observe mode (one side only)

```yaml
mode: observe
observe_side: target             # or source
target:
  kind: salesforce
  url: ${SALESFORCE_URL}
  api_version: ${SALESFORCE_API_VERSION}
  client_id: ${SALESFORCE_CLIENT_ID}
  client_secret: ${SALESFORCE_CLIENT_SECRET}
# omit the other connection
```

CLI override: `migrate-prove run suite.yaml --observe target`.

## Unsupported today — do not fake

| Requested | Action |
| --- | --- |
| Excel / files as a live connector | Record blocker; keep questionnaire answer; no bogus `kind` |
| Drivers / extras not installed | Note: `pip install migrate-prove[postgres]` / `[athena]` / `[http]` / `[salesforce]` |

## Dialect hints (non-exhaustive)

| System | Typical dialect / URL scheme |
| --- | --- |
| Postgres | `postgresql+psycopg` |
| Athena | `awsathena+rest` (PyAthena); AWS env/profile for creds |
| MSSQL | `mssql+pyodbc://…` (driver-dependent) |
| SQLite demo | `sqlite:///source.db` |
| Salesforce mock | `kind: salesforce_mock` + `fixtures` |
| Live Salesforce | `kind: salesforce` + url / api_version / client_id / client_secret |
| HTTP API | `kind: http` + base_url / auth / resources |
| One-sided | `mode: observe` + `observe_side` |

## Output checklist

- [ ] Suite YAML uses `${VAR}` for secrets
- [ ] `.env.example` lists every var (mirror root `.env.example` stubs)
- [ ] Extra noted when `salesforce` / `http` / `postgres` / `athena` required
- [ ] Observe vs prove mode recorded
- [ ] Unsupported kinds listed as blockers for Prover
