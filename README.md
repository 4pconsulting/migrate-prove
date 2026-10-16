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

## Install

```powershell
pip install migrate-prove
pip install "migrate-prove[postgres]"     # psycopg
pip install "migrate-prove[athena]"       # PyAthena SQLAlchemy dialect
pip install "migrate-prove[excel]"        # openpyxl STM import
pip install "migrate-prove[salesforce]"   # live Salesforce REST (httpx)
pip install "migrate-prove[http]"         # declarative HTTP APIs (httpx + jmespath)
```

Extras are additive — install what your suite `kind` needs. Demos and their YAML live in this repository; clone it and follow the quick start below.

## Releasing

Bump `version` in `pyproject.toml` in the pull request. Merging that change to `main` publishes the new version to PyPI. A push that does not change the version does not upload again.

## Quick start

Ensure you are in the root `migrate-prove` directory.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
migrate-prove seed-demo examples/banking_demo
migrate-prove compile examples/banking_demo/stm.yaml
migrate-prove run examples/banking_demo/suite.yaml
```

Open `examples/banking_demo/artifacts/report.html`. You should see a **passing total count** and failing sliced volume, transforms, hashes, probes, and invariants — that is the point.

### Hops vs end-to-end

Layered pipelines need both thin hop gates and a finalised source → target STM. Runnable demo (hops pass; e2e fails on status misclassification):

```powershell
migrate-prove seed-hops-demo examples/hops_vs_e2e
migrate-prove run examples/hops_vs_e2e/suites/hop_01.yaml
migrate-prove run examples/hops_vs_e2e/suites/hop_02.yaml
migrate-prove run examples/hops_vs_e2e/suites/e2e.yaml
```

See [examples/hops_vs_e2e/README.md](examples/hops_vs_e2e/README.md) and [ASSURANCE.md](ASSURANCE.md).

### CTE source + Salesforce (mock) target

Not every migration is table→table. A third demo joins two source tables via a **CTE** and proves against **paginated Salesforce JSON fixtures**:

```powershell
migrate-prove seed-cte-api-demo examples/cte_api_demo
migrate-prove run examples/cte_api_demo/suite.yaml
```

See [examples/cte_api_demo/README.md](examples/cte_api_demo/README.md) and [ADVANCED.md](ADVANCED.md).

```powershell
pytest
```

## Connections and secrets

Suite YAML must **never** contain passwords. Reference environment variables with `${VAR}` or `${VAR:-default}`. Locally, copy [`.env.example`](.env.example) to `.env` (gitignored) — that file lists every supported secret/var pattern (Postgres, Athena, live Salesforce, HTTP). In CI, set the same names from the pipeline secrets store — `python-dotenv` only fills gaps; real process env always wins.

Optional: `migrate-prove run suite.yaml --env-file path\to\.env`. The CLI prints **redacted** connection labels.

### Setup matrix

| Situation | Suite `kind` / mode | Extra | Env vars (see `.env.example`) | Details |
| --- | --- | --- | --- | --- |
| Postgres / SQL URL | `sqlalchemy` (default) | `[postgres]` if needed | `SOURCE_URL` / `TARGET_URL` or structured `*_HOST`… | below |
| Athena | `sqlalchemy` URL | `[athena]` | `AWS_*`, `ATHENA_*` | below |
| SQLite demos | `url: sqlite:///…` | none | none | quick start |
| Salesforce fixtures (CI) | `salesforce_mock` | none | none (`fixtures:` path) | [ADVANCED.md](ADVANCED.md) |
| Live Salesforce | `salesforce` | `[salesforce]` | `SALESFORCE_URL`, `SALESFORCE_API_VERSION`, `SALESFORCE_CLIENT_ID`, `SALESFORCE_CLIENT_SECRET` | [ADVANCED.md](ADVANCED.md) |
| Raw HTTP API | `http` | `[http]` | `API_BASE_URL`, `API_TOKEN` (or header/basic vars) | [ADVANCED.md](ADVANCED.md) |
| Only one side reachable | `run: observe-source` or `observe-target` | as for that side | same as live side | [ADVANCED.md](ADVANCED.md) |
| Grain split across tables | entity `source_parts` / `target_parts` | — | — | [ADVANCED.md](ADVANCED.md) |

Unsupported as live connectors today (record a blocker; do not invent `kind`): Excel/files, arbitrary undocumented APIs without an `http` resource map.

### SQLAlchemy (default)

**URL form:**

```yaml
source:
  url: "postgresql+psycopg://${SOURCE_USER}:${SOURCE_PASSWORD}@${SOURCE_HOST}:${SOURCE_PORT:-5432}/${SOURCE_DB}"
target:
  url: "${TARGET_URL}"
```

**Structured form:**

```yaml
source:
  dialect: postgresql+psycopg
  host: ${SOURCE_HOST}
  port: ${SOURCE_PORT:-5432}
  database: ${SOURCE_DB}
  username: ${SOURCE_USER}
  password: ${SOURCE_PASSWORD}
  options:
    sslmode: require
```

Athena stays URL-shaped; AWS credentials come from the environment or the runner IAM role:

```yaml
target:
  url: "awsathena+rest://athena.${AWS_REGION}.amazonaws.com:443/${ATHENA_SCHEMA}?s3_staging_dir=${ATHENA_S3_STAGING}&work_group=${ATHENA_WORKGROUP}"
```

### API targets

```yaml
# CI-safe mock — no secrets
target:
  kind: salesforce_mock
  fixtures: fixtures/salesforce

# Live Salesforce — pip install "migrate-prove[salesforce]"
target:
  kind: salesforce
  url: ${SALESFORCE_URL}
  api_version: ${SALESFORCE_API_VERSION}
  client_id: ${SALESFORCE_CLIENT_ID}
  client_secret: ${SALESFORCE_CLIENT_SECRET}

# Declarative HTTP — pip install "migrate-prove[http]"
target:
  kind: http
  base_url: ${API_BASE_URL}
  auth:
    type: bearer                    # bearer | header | basic
    token: ${API_TOKEN}
    # header_name: X-Api-Key
    # header_value: ${API_KEY}
    # username: ${API_USER}
    # password: ${API_PASSWORD}
  resources:
    Account:                        # key = target_table / object name
      list_path: /v1/accounts
      schema_path: /v1/accounts/schema   # optional
      records_jmespath: data
      next_jmespath: next
```

### Run intent (suite `run` field)

The suite declares what kind of run this is — not the CLI:

| `run` | Connections | Behaviour |
| --- | --- | --- |
| `prove` (default) | source **and** target | Full pairwise checks |
| `observe-target` | target only | Export target rows; pairwise SKIP |
| `observe-source` | source only | Export source rows; pairwise SKIP |

```yaml
# Full prove — e.g. Athena → Salesforce
run: prove
source:
  url: "awsathena+rest://..."
target:
  kind: salesforce
  url: ${SALESFORCE_URL}
  # …

# One-sided export when the other side is unreachable
run: observe-target
target:
  kind: salesforce
  url: ${SALESFORCE_URL}
  # … (omit source)
```

Exports land under `artifacts/observe/`. Use separate suite files for prove vs observe; share the same `contract`.

## Authoring a contract

See `examples/banking_demo/stm.yaml`. A suite file points at the contract and the two connections (SQLite needs no secrets):

```yaml
name: my-cutover
contract: stm.yaml
source: { url: sqlite:///source.db }
target: { url: "${TARGET_URL}" }
output_dir: artifacts
```

Transform rules are the oracle, not a second copy of ETL Python. Keep ETL and this file aligned; do not reimplement warehouse SQL ad hoc in tests.

## Roadmap (v1 is the skeleton that already fails the right way)

1. **Now** — STM YAML, SQLAlchemy + Salesforce mock/live + HTTP connectors, CTE `source_sql`, env-backed secrets, L1–L5, hashing, probes, CLI, HTML/JSON, demos.
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
- Never commit secrets; suite YAML references `${ENV}` only.
