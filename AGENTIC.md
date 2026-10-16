# Using migrate-prove agents

migrate-prove stays a deterministic validation engine. The agentic layer helps you **author** the STM (test oracle) and suite YAML from mapping docs and connection answers — then run and triage. Agents do not decide “looks correct”; [`ValidationEngine`](src/migrate_prove/engine.py) does.

Portable prompts and skills live under [`agentic/`](agentic/). Cursor discovers them via thin wrappers in [`.cursor/agents/`](.cursor/agents/) and [`.cursor/skills/`](.cursor/skills/).

## Pipeline

```text
                    Orchestrator
                         │
         ┌───────────────┼───────────────┐
         ▼               ▼               ▼
Docs → Collator → Oracle → Suite → Prover → scorecard
         │               │         │
         └──────── questions funnel back to you ─┘
```

A full STM is not required to start. Skip stages you have already finished.

## How to use in Cursor

### Run to completion (recommended)

Start **`migrate-prove-orchestrator`**. It:

1. Delegates Collator → Oracle → Suite → Prover one stage at a time
2. Checks gates under [`agentic/handoff/`](agentic/handoff/)
3. Surfaces specialist **QUESTIONS** / **BLOCKERS** to you, waits for answers, then resumes
4. Skips stages whose artifacts already satisfy the gate

Ask it to “run the engagement to completion” and point at your docs or an existing STM/suite.

### Subagents (manual handoff)

Use these when you want a single stage only:

| Agent | When |
| --- | --- |
| `migrate-prove-orchestrator` | Full pipeline with question funnel |
| `migrate-prove-collator` | Mapping docs exist; no complete STM yet |
| `migrate-prove-oracle` | Dossier ready; author or deepen `stm.yaml` |
| `migrate-prove-suite` | STM compiles; need connections + `suite.yaml` |
| `migrate-prove-prover` | Suite ready; compile, run, triage scorecard |

Each wrapper points at the full prompt in [`agentic/agents/`](agentic/agents/). Product facts: [`agentic/AGENTS.md`](agentic/AGENTS.md). Specialists do **not** start the next agent themselves — only the orchestrator (or you) advances the pipeline.

### Skills

Load with `/` when you need one job only, for example:

| Skill | Job |
| --- | --- |
| `/read-pdf`, `/read-excel`, `/read-word` | Extract into a dossier |
| `/prove-schema` … `/prove-invariants` | Fill one STM dimension |
| `/elicit-connections` | Interview for Athena / MSSQL / SF / … |
| `/run-prove`, `/triage-scorecard` | Execute and interpret results |

Source of truth: [`agentic/skills/*/SKILL.md`](agentic/skills/). Do not edit only the `.cursor` wrapper.

## Typical engagement

**Preferred:** start **`migrate-prove-orchestrator`** with your inputs; answer questions when it pauses.

Manual equivalent:

1. **Have mapping documents** — Start **Collator**. Point it at PDFs, workbooks, Word specs. It writes a dossier (see [`agentic/templates/dossier.md`](agentic/templates/dossier.md)). Gate: [`agentic/handoff/dossier.schema.md`](agentic/handoff/dossier.schema.md).
2. **Implement the oracle** — Hand the dossier to **Oracle**. Load `prove-*` skills per dimension as needed. Gate: `migrate-prove compile` succeeds and [`agentic/handoff/stm.checklist.md`](agentic/handoff/stm.checklist.md).
3. **Bind environments** — **Suite** always runs `elicit-connections` for source and target. Secrets only as `${ENV}`. Gate: [`agentic/handoff/suite.checklist.md`](agentic/handoff/suite.checklist.md).
4. **Prove** — **Prover** seeds (if demo), runs the suite, triages by concern (`migration` | `transformation` | `reconciliation`). Propose STM diffs; do not silent-edit the oracle.

**Already have an STM?** Tell the orchestrator to skip Collator (and Oracle if the STM already compiles) — e.g. the banking demo.

## Try it (no secrets)

See [`agentic/SMOKE.md`](agentic/SMOKE.md) for banking, hops-vs-e2e, and CTE/Salesforce-mock paths. Quick banking smoke:

```powershell
python -m pip install -e ".[dev]"
migrate-prove seed-demo examples/banking_demo
migrate-prove compile examples/banking_demo/stm.yaml
migrate-prove run examples/banking_demo/suite.yaml
```

Open `examples/banking_demo/artifacts/report.html`. Totals may pass while slices/transforms/hashes fail — that is intentional.

## Rules of the road

- STM YAML is the oracle; agents author YAML, not ad-hoc pytest/SQL test suites.
- Never put passwords or tokens in suite/STM YAML — `${VAR}` only.
- Do not invent financial tolerances; defer L3 / exclude `"3"` from `levels` until agreed.
- Cap connectors to what the package supports today (`sqlalchemy`, `salesforce_mock`, `salesforce`, `http`); mark other kinds as blockers.
- Use suite `mode: observe` + `observe_side` for one-sided export; use `source_parts` / `target_parts` when one grain is split across objects.
- Hop contracts stay thin; e2e carries the cutover claim — see [`ASSURANCE.md`](ASSURANCE.md).

## Further reading

| Doc | Role |
| --- | --- |
| [`agentic/README.md`](agentic/README.md) | Layout and design of the agentic folder |
| [`agentic/SMOKE.md`](agentic/SMOKE.md) | Known-good demo handoffs |
| [`README.md`](README.md) | Product install, CLI, architecture |
| [`ASSURANCE.md`](ASSURANCE.md) | Hops vs end-to-end |
