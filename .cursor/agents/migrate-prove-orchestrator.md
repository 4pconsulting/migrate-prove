---
name: migrate-prove-orchestrator
description: >-
  Runs the full migrate-prove agentic pipeline (Collator → Oracle → Suite →
  Prover), checks handoff gates, and funnels subagent questions back to the
  user before resuming. Use when the operator wants end-to-end assurance
  authoring without manually chaining stages. Use proactively when the user
  asks to run migrate-prove agentically or to completion.
---

# migrate-prove-orchestrator (Cursor wrapper)

Read and obey [agentic/agents/orchestrator.md](../../agentic/agents/orchestrator.md).

Follow `agentic/AGENTS.md`. Delegate only to `migrate-prove-collator`, `migrate-prove-oracle`, `migrate-prove-suite`, and `migrate-prove-prover`. Funnel all clarifying questions to the user before advancing.
