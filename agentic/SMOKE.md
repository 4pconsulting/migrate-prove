# Smoke path — known-good handoffs

Use these repo demos to prove the agentic pipeline without new mapping documents. No secrets required (SQLite / fixtures).

## Path A — Banking (full STM already present)

Simulates **Oracle → Suite → Prover** when the oracle already exists (skip Collator, or treat `stm.yaml` as dossier output).

```powershell
# from repo root, editable install
python -m pip install -e ".[dev]"
migrate-prove seed-demo examples/banking_demo
migrate-prove compile examples/banking_demo/stm.yaml
migrate-prove run examples/banking_demo/suite.yaml
```

Open `examples/banking_demo/artifacts/report.html`.

**Expected:** total volume can pass while sliced volume, transforms, hashes, probes, and/or invariants fail — planted status misclassification. Triage with `triage-scorecard` (concern: mostly **transformation**).

### Agent mapping

| Stage | Artifact | Notes |
| --- | --- | --- |
| Collator | optional | Invent a short dossier that cites banking STM fields |
| Oracle | `examples/banking_demo/stm.yaml` | Already complete; `compile` is the gate |
| Suite | `examples/banking_demo/suite.yaml` | SQLite URLs — elicit-connections would record “SQLite file” |
| Prover | `artifacts/report.*` | Exit code 1 expected |

## Path B — Hops vs e2e

```powershell
migrate-prove seed-hops-demo examples/hops_vs_e2e
migrate-prove run examples/hops_vs_e2e/suites/hop_01.yaml
migrate-prove run examples/hops_vs_e2e/suites/hop_02.yaml
migrate-prove run examples/hops_vs_e2e/suites/e2e.yaml
```

**Expected:** hops exit 0; e2e fails on sliced status volume while totals may still match. Prover must not thin the e2e STM to silence that without stakeholder agreement (`ASSURANCE.md`).

## Path C — CTE + Salesforce mock

```powershell
migrate-prove seed-cte-api-demo examples/cte_api_demo
migrate-prove run examples/cte_api_demo/suite.yaml
```

**Suite / elicit-connections lesson:** target is `kind: salesforce_mock` with `fixtures` — not a SQL URL.

## Collator-only dry run (no PDFs required)

1. Copy `agentic/templates/dossier.md` to a scratch path.
2. Fill Entities / Mappings from `examples/banking_demo/stm.yaml` as if extracted from docs.
3. Confirm `agentic/handoff/dossier.schema.md` required sections.
4. Hand to Oracle (which can no-op to the existing STM) → Suite → Prover as Path A.

## Checklist

- [ ] `agentic/README.md` pipeline understood
- [ ] Cursor wrappers under `.cursor/agents` and `.cursor/skills` resolve
- [ ] Path A compile + run produces HTML scorecard
- [ ] Triage names at least one concern tag correctly
