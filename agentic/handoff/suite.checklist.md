# Suite checklist (Suite → Prover gate)

Artifact: `suite.yaml` + `.env.example` (never commit real secrets).

## Hard gate

- [ ] `contract` path resolves relative to the suite file
- [ ] Source and target connections present
- [ ] Secrets referenced only as `${VAR}` (or documented as local URLs for demos)
- [ ] `.env.example` lists every required variable name
- [ ] `levels` match what the STM is ready for (see stm.checklist deferred items)
- [ ] `output_dir` set
- [ ] Unsupported connection kinds recorded as blockers (do not fake YAML)

## Connection elicitation

- [ ] `elicit-connections` skill was used for source and target
- [ ] Kind recorded (Athena, MSSQL, Postgres, SQLite, Salesforce mock, other API, …)
- [ ] Mapped to supported `ConnectionConfig` **or** marked unsupported
- [ ] Username/password/token env var names agreed

## Prover may proceed when

Suite YAML validates conceptually against `SuiteConfig` and required env vars are documented. Live credentials are the operator’s responsibility.
