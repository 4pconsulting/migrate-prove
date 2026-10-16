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

## Interview (ask per side: source and target)

1. **Role:** source or target?
2. **System kind:** Athena | MSSQL | Postgres | SQLite | other SQL | Salesforce | other API | Excel/files | unknown
3. **Shape:**
   - Table(s) vs CTE / custom SQL (`source_sql` lives on the **entity**, not the connection)
   - For Salesforce: mock fixtures path vs live API
   - For other APIs: list/schema paths, pagination JMESPath, auth type
4. **How to connect:** full SQLAlchemy URL vs structured dialect/host/port/database
5. **Secrets:** env var names for user/password/token (never paste production secrets into chat logs if avoidable — prefer var names)
6. **Extras:** query options, workgroup, SSL, catalog (record under `options` when using structured form); for API connectors optional `buffer_rows` / `spill_dir` / `max_rows`

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

### Salesforce mock

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

### Declarative HTTP (`pip install migrate-prove[http]`)

```yaml
target:
  kind: http
  base_url: ${API_BASE_URL}
  auth:
    type: bearer
    token: ${API_TOKEN}
  resources:
    Account:
      list_path: /v1/accounts
      schema_path: /v1/accounts/schema   # optional
      records_jmespath: data
      next_jmespath: next
```

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

## Output checklist

- [ ] Suite YAML uses `${VAR}` for secrets
- [ ] `.env.example` lists every var
- [ ] Unsupported kinds listed as blockers for Prover
