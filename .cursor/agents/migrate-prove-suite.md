---
name: migrate-prove-suite
description: >-
  Builds migrate-prove suite.yaml and .env.example from an STM by eliciting
  source/target connection details (Athena, MSSQL, Postgres, Salesforce, APIs,
  etc.). Use after the Oracle agent produces a compiling stm.yaml.
---

# migrate-prove-suite (Cursor wrapper)

Read and obey [agentic/agents/suite.md](../../agentic/agents/suite.md).

Follow `agentic/AGENTS.md`. Always load `elicit-connections`. Handoff gate: `agentic/handoff/suite.checklist.md`.
