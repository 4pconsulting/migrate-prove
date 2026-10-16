from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Literal

from migrate_prove.checks import (
    check_failure_probes,
    check_invariants,
    check_metrics,
    check_row_hashes,
    check_schema,
    check_transforms,
    check_volume,
)
from migrate_prove.connectors import Connector
from migrate_prove.models import CheckResult, CheckStatus, Concern, Contract, Entity, RiskLevel
from migrate_prove.observe import export_observation, skip_pairwise
from migrate_prove.progress import ProgressReporter
from migrate_prove.sql import inspect_relations, source_relations, target_relations


LEVEL_ORDER = ["observe", "1", "probes", "2", "3", "4", "hash", "5"]

LEVEL_TITLES = {
    "observe": "Observation export",
    "1": "Level 1 — Structural & schema",
    "probes": "Failure-mode probes",
    "2": "Level 2 — Volume & cardinality",
    "3": "Level 3 — Metric & financial reconciliation",
    "4": "Level 4 — Transformation rules",
    "hash": "Row-level cryptographic reconciliation",
    "5": "Level 5 — Business invariants",
}

PAIRWISE_LEVELS = {"1", "probes", "2", "3", "4", "hash"}

_OBSERVE_SKIP_TITLES = {
    "1": "Schema (skipped — observe)",
    "probes": "Failure-mode probes (skipped — observe)",
    "2": "Volume (skipped — observe)",
    "3": "Metrics (skipped — observe)",
    "4": "Transforms (skipped — observe)",
    "hash": "Row hashes (skipped — observe)",
}

RunKind = Literal["prove", "observe-source", "observe-target"]


@dataclass
class RunReport:
    name: str
    started_at: str
    finished_at: str = ""
    run: str = "prove"
    results: list[CheckResult] = field(default_factory=list)

    @property
    def passed(self) -> int:
        return sum(1 for item in self.results if item.status == CheckStatus.PASS)

    @property
    def failed(self) -> int:
        return sum(1 for item in self.results if item.status in {CheckStatus.FAIL, CheckStatus.ERROR})

    @property
    def skipped(self) -> int:
        return sum(1 for item in self.results if item.status == CheckStatus.SKIP)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def is_observe(self) -> bool:
        return self.run in {"observe-source", "observe-target"}

    @property
    def observe_side(self) -> str | None:
        if self.run == "observe-source":
            return "source"
        if self.run == "observe-target":
            return "target"
        return None

    def by_level(self) -> dict[str, list[CheckResult]]:
        grouped: dict[str, list[CheckResult]] = {}
        for item in self.results:
            grouped.setdefault(item.level, []).append(item)
        return grouped

    def critical_failed(self, contract: Contract) -> list[str]:
        critical = {entity.name for entity in contract.entities if entity.risk == RiskLevel.critical}
        return [
            item.id
            for item in self.results
            if item.entity in critical and item.status in {CheckStatus.FAIL, CheckStatus.ERROR}
        ]

    def observation_exports(self) -> list[dict[str, Any]]:
        exports: list[dict[str, Any]] = []
        for item in self.results:
            if item.level == "observe" and item.status == CheckStatus.PASS:
                actual = item.actual
                if isinstance(actual, dict):
                    row_count = actual.get("row_count")
                else:
                    row_count = actual
                if row_count is None:
                    row_count = item.evidence.get("row_count")
                exports.append(
                    {
                        "entity": item.entity,
                        "path": item.evidence.get("path"),
                        "meta": item.evidence.get("meta"),
                        "row_count": row_count,
                        "sample": (
                            actual.get("sample")
                            if isinstance(actual, dict)
                            else None
                        )
                        or (item.evidence.get("sample") or [None])[0],
                        "message": item.message,
                    }
                )
        return exports

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "run": self.run,
            "summary": {
                "total": self.total,
                "passed": self.passed,
                "failed": self.failed,
                "skipped": self.skipped,
                "pass_rate": round(self.passed / self.total, 4) if self.total else 0,
            },
            "observations": self.observation_exports(),
            "results": [item.model_dump() for item in self.results],
        }


@dataclass
class _StepPlan:
    entity: Entity
    level: str
    label: str


class ValidationEngine:
    def __init__(
        self,
        source: Connector | None,
        target: Connector | None,
        *,
        run: RunKind = "prove",
        output_dir: Path | None = None,
        progress: ProgressReporter | None = None,
    ) -> None:
        self.source = source
        self.target = target
        self.run_kind: RunKind = run
        self.output_dir = output_dir or Path("artifacts")
        self.progress = progress
        if run == "prove":
            if source is None or target is None:
                raise ValueError("run: prove requires both source and target connectors")
        elif run == "observe-source" and source is None:
            raise ValueError("run: observe-source requires a source connector")
        elif run == "observe-target" and target is None:
            raise ValueError("run: observe-target requires a target connector")

    def run(
        self,
        contract: Contract,
        *,
        levels: list[str] | None = None,
        continue_on_fail: bool = True,
        entities: list[str] | None = None,
    ) -> RunReport:
        selected = levels or [level for level in LEVEL_ORDER if level != "observe"]
        if self.run_kind != "prove" and "observe" not in selected:
            selected = ["observe", *selected]
        report = RunReport(
            name=contract.name,
            started_at=datetime.now(UTC).isoformat(),
            run=self.run_kind,
        )
        selected_entities = [
            entity
            for entity in contract.entities
            if entities is None or entity.name in entities
        ]
        plan = self._plan_steps(selected_entities, selected)
        total_steps = len(plan)
        step_index = 0
        if self.progress is not None:
            self.progress.begin(total_steps)

        for entity in selected_entities:
            entity_results = self._run_entity(
                entity,
                selected,
                continue_on_fail,
                plan_total=total_steps,
                step_index=step_index,
            )
            report.results.extend(entity_results.results)
            step_index = entity_results.next_step_index
            if not continue_on_fail and any(
                item.status in {CheckStatus.FAIL, CheckStatus.ERROR} for item in entity_results.results
            ):
                break
        report.finished_at = datetime.now(UTC).isoformat()
        return report

    def _plan_steps(self, entities: list[Entity], levels: list[str]) -> list[_StepPlan]:
        steps: list[_StepPlan] = []
        for entity in entities:
            if self.run_kind == "prove":
                for level in LEVEL_ORDER:
                    if level not in levels or level == "observe":
                        continue
                    steps.append(_StepPlan(entity, level, LEVEL_TITLES[level]))
                continue

            if "observe" in levels:
                steps.append(_StepPlan(entity, "observe", LEVEL_TITLES["observe"]))
            for level in LEVEL_ORDER:
                if level not in levels or level in {"observe", "5"}:
                    continue
                if level in PAIRWISE_LEVELS:
                    steps.append(_StepPlan(entity, level, _OBSERVE_SKIP_TITLES[level]))
            if "5" in levels:
                steps.append(_StepPlan(entity, "5", LEVEL_TITLES["5"]))
        return steps

    def _run_entity(
        self,
        entity: Entity,
        levels: list[str],
        continue_on_fail: bool,
        *,
        plan_total: int,
        step_index: int,
    ) -> "_EntityRun":
        if self.run_kind != "prove":
            return self._run_entity_observe(
                entity,
                levels,
                continue_on_fail,
                plan_total=plan_total,
                step_index=step_index,
            )
        return self._run_entity_prove(
            entity,
            levels,
            continue_on_fail,
            plan_total=plan_total,
            step_index=step_index,
        )

    def _run_step(
        self,
        *,
        entity: Entity,
        level: str,
        label: str,
        plan_total: int,
        step_index: int,
        runner: Callable[[], list[CheckResult]],
    ) -> tuple[list[CheckResult], int]:
        current = step_index + 1
        if self.progress is not None:
            self.progress.step_start(
                current,
                plan_total,
                entity=entity.name,
                level=level,
                label=label,
            )
        try:
            batch = runner()
        except Exception as exc:
            if self.progress is not None:
                self.progress.step_error(
                    current,
                    plan_total,
                    entity=entity.name,
                    level=level,
                    label=label,
                    error=exc,
                )
            raise
        if self.progress is not None:
            self.progress.step_finish(current, plan_total, batch)
        return batch, current

    def _run_entity_prove(
        self,
        entity: Entity,
        levels: list[str],
        continue_on_fail: bool,
        *,
        plan_total: int,
        step_index: int,
    ) -> "_EntityRun":
        assert self.source is not None and self.target is not None
        results: list[CheckResult] = []
        source_info = None
        target_info = None
        try:
            source_info = inspect_relations(self.source, source_relations(entity))
            target_info = inspect_relations(self.target, target_relations(entity))
        except Exception:
            source_info = target_info = None

        runners: dict[str, Callable[[], list[CheckResult]]] = {
            "1": lambda: check_schema(entity, self.source, self.target),
            "probes": lambda: check_failure_probes(
                entity, self.source, self.target, source_info, target_info
            ),
            "2": lambda: check_volume(entity, self.source, self.target),
            "3": lambda: check_metrics(entity, self.source, self.target),
            "4": lambda: check_transforms(entity, self.source, self.target),
            "hash": lambda: check_row_hashes(entity, self.source, self.target),
            "5": lambda: check_invariants(entity, self.target),
        }
        for level in LEVEL_ORDER:
            if level not in levels or level == "observe":
                continue
            batch, step_index = self._run_step(
                entity=entity,
                level=level,
                label=LEVEL_TITLES[level],
                plan_total=plan_total,
                step_index=step_index,
                runner=runners[level],
            )
            results.extend(batch)
            if not continue_on_fail and any(
                item.status in {CheckStatus.FAIL, CheckStatus.ERROR} for item in batch
            ):
                break
        return _EntityRun(results=results, next_step_index=step_index)

    def _run_entity_observe(
        self,
        entity: Entity,
        levels: list[str],
        continue_on_fail: bool,
        *,
        plan_total: int,
        step_index: int,
    ) -> "_EntityRun":
        side: Literal["source", "target"] = (
            "source" if self.run_kind == "observe-source" else "target"
        )
        results: list[CheckResult] = []
        live = self.source if side == "source" else self.target
        assert live is not None
        missing = "target" if side == "source" else "source"

        if "observe" in levels:
            batch, step_index = self._run_step(
                entity=entity,
                level="observe",
                label=LEVEL_TITLES["observe"],
                plan_total=plan_total,
                step_index=step_index,
                runner=lambda: [export_observation(entity, live, side, self.output_dir)],
            )
            results.extend(batch)
            if not continue_on_fail and any(
                item.status in {CheckStatus.FAIL, CheckStatus.ERROR} for item in batch
            ):
                return _EntityRun(results=results, next_step_index=step_index)

        for level in LEVEL_ORDER:
            if level not in levels or level in {"observe", "5"}:
                continue
            if level in PAIRWISE_LEVELS:
                batch, step_index = self._run_step(
                    entity=entity,
                    level=level,
                    label=_OBSERVE_SKIP_TITLES[level],
                    plan_total=plan_total,
                    step_index=step_index,
                    runner=lambda lvl=level: [
                        skip_pairwise(entity, lvl, _OBSERVE_SKIP_TITLES[lvl], missing)
                    ],
                )
                results.extend(batch)

        if "5" in levels:
            def _invariants() -> list[CheckResult]:
                if side == "target" and len(target_relations(entity)) == 1:
                    return check_invariants(entity, live)
                return [
                    CheckResult(
                        id=f"5.{entity.name}.observe_skip",
                        level="5",
                        title="Invariants (skipped — observe)",
                        entity=entity.name,
                        concern=Concern.reconciliation,
                        status=CheckStatus.SKIP,
                        message=(
                            "observe run: invariants need run: observe-target "
                            "and a single target relation"
                        ),
                    )
                ]

            batch, step_index = self._run_step(
                entity=entity,
                level="5",
                label=LEVEL_TITLES["5"],
                plan_total=plan_total,
                step_index=step_index,
                runner=_invariants,
            )
            results.extend(batch)
        return _EntityRun(results=results, next_step_index=step_index)


@dataclass
class _EntityRun:
    results: list[CheckResult]
    next_step_index: int
