# Suite checklist (Suite → Prover gate)

Artifact: `suite.yaml` + `.env.example` (never commit real secrets).

## Hard gate

- [ ] `contract` path resolves relative to the suite file
- [ ] Connections match `run`:
  - [ ] `run: prove` (default) → both `source` and `target` present
  - [ ] `run: observe-source` → `source` present (target may be omitted)
  - [ ] `run: observe-target` → `target` present (source may be omitted)
- [ ] Secrets referenced only as `${VAR}` (or documented as local URLs for demos)
- [ ] `.env.example` lists every required variable name (align with repo root `.env.example` stubs for SF/HTTP/SQL)
- [ ] Required pip extras noted when using `salesforce` / `http` / `postgres` / `athena`
- [ ] `levels` match what the STM is ready for (see stm.checklist deferred items)
- [ ] `output_dir` set
- [ ] Unsupported connection kinds recorded as blockers (do not fake YAML)

## Connection elicitation

- [ ] `elicit-connections` skill was used for source and target (or the single observe side)
- [ ] Kind recorded (Athena, MSSQL, Postgres, SQLite, Salesforce mock/live, HTTP, …)
- [ ] Mapped to supported `ConnectionConfig` **or** marked unsupported
- [ ] Username/password/token env var names agreed
- [ ] Auth type recorded for HTTP (`bearer` | `header` | `basic`) when applicable

## Prover may proceed when

Suite YAML validates conceptually against `SuiteConfig` and required env vars are documented. Live credentials are the operator’s responsibility.
