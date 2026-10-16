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
4. **How to connect:** full SQLAlchemy URL vs structured dialect/host/port/database
5. **Secrets:** env var names for user/password/token (never paste production secrets into chat logs if avoidable — prefer var names)
6. **Extras:** query options, workgroup, SSL, catalog (record under `options` when using structured form)

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

### Salesforce mock (supported today)

```yaml
target:
  kind: salesforce_mock
  fixtures: fixtures/salesforce
```

## Unsupported today — do not fake

| Requested | Action |
| --- | --- |
| Live Salesforce / arbitrary REST | Record blocker; keep questionnaire answer; no bogus `kind` |
| Drivers not installed | Note extra: `pip install migrate-prove[postgres]` / `[athena]` / etc. |

## Dialect hints (non-exhaustive)

| System | Typical dialect / URL scheme |
| --- | --- |
| Postgres | `postgresql+psycopg` |
| Athena | `awsathena+rest` (PyAthena); AWS env/profile for creds |
| MSSQL | `mssql+pyodbc://…` (driver-dependent) |
| SQLite demo | `sqlite:///source.db` |
| Salesforce mock | `kind: salesforce_mock` + `fixtures` |

## Output checklist

- [ ] Suite YAML uses `${VAR}` for secrets
- [ ] `.env.example` lists every var
- [ ] Unsupported kinds listed as blockers for Prover
