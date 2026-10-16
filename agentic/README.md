# Agentic migrate-prove

Staged agents and skills that turn mapping documents into an STM oracle, a connection-aware suite, and a migrate-prove scorecard.

The Python engine still executes checks. Agents author YAML; they do not judge “looks correct.”

## Pipeline

```text
                 Orchestrator (question funnel)
                            │
Docs → Collator → Oracle → Suite → Prover → scorecard
```

Flat handoffs only: Orchestrator → one specialist → Orchestrator. Specialists return **QUESTIONS** when blocked; the Orchestrator asks the user, then resumes. A full STM is not required to start; early stages may be incomplete.

## Layout

| Path | Role |
| --- | --- |
| [AGENTS.md](AGENTS.md) | Always-on facts for any agent in this repo |
| [agents/](agents/) | Orchestrator, Collator, Oracle, Suite, Prover prompts |
| [skills/](skills/) | Document, dimension, connection, run, triage skills |
| [handoff/](handoff/) | Artifact schemas and completeness gates |
| [templates/](templates/) | Starting dossiers, STM, suite, env |
| [SMOKE.md](SMOKE.md) | Known-good path using banking / hops / cte_api demos |

Cursor discovery stubs live under [`.cursor/`](../.cursor/) and point here.

## How to invoke

**Preferred:** start the **Orchestrator** (`migrate-prove-orchestrator`) and ask it to run to completion. It delegates stages, checks gates, and funnels questions to you.

Manual:

1. Start with the **Collator** agent (or `/read-pdf`, `/read-excel`, `/read-word` on inputs).
2. Hand the dossier to the **Oracle** agent; load `prove-*` skills per dimension as needed.
3. Hand the STM to the **Suite** agent; it must run `elicit-connections`.
4. Hand suite + STM to the **Prover** agent (`run-prove`, then `triage-scorecard`).

Gates before each handoff are in [handoff/](handoff/). See also root [AGENTIC.md](../AGENTIC.md).

## What this is not

- Not a replacement for `ValidationEngine`
- Not a place for secrets in YAML
- Not a claim that every system (e.g. Excel as a live connector) is implemented — elicit honestly, map to supported kinds (`sqlalchemy`, `salesforce_mock`, `salesforce`, `http`) or mark unsupported
