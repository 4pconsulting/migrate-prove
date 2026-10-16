from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

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


@dataclass
class RunReport:
    name: str
    started_at: str
    finished_at: str = ""
    mode: str = "prove"
    observe_side: str | None = None
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
                exports.append(
                    {
                        "entity": item.entity,
                        "path": item.evidence.get("path"),
                        "meta": item.evidence.get("meta"),
                        "row_count": item.actual,
                        "message": item.message,
                    }
                )
        return exports

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "mode": self.mode,
            "observe_side": self.observe_side,
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


class ValidationEngine:
    def __init__(
        self,
        source: Connector | None,
        target: Connector | None,
        *,
        mode: Literal["prove", "observe"] = "prove",
        observe_side: Literal["source", "target"] | None = None,
        output_dir: Path | None = None,
    ) -> None:
        self.source = source
        self.target = target
        self.mode = mode
        self.observe_side = observe_side
        self.output_dir = output_dir or Path("artifacts")
        if mode == "prove":
            if source is None or target is None:
                raise ValueError("prove mode requires both source and target connectors")
        elif observe_side is None:
            raise ValueError("observe mode requires observe_side")
        elif observe_side == "source" and source is None:
            raise ValueError("observe mode (source) requires a source connector")
        elif observe_side == "target" and target is None:
            raise ValueError("observe mode (target) requires a target connector")

    def run(
        self,
        contract: Contract,
        *,
        levels: list[str] | None = None,
        continue_on_fail: bool = True,
        entities: list[str] | None = None,
    ) -> RunReport:
        selected = levels or [level for level in LEVEL_ORDER if level != "observe"]
        if self.mode == "observe" and "observe" not in selected:
            selected = ["observe", *selected]
        report = RunReport(
            name=contract.name,
            started_at=datetime.now(UTC).isoformat(),
            mode=self.mode,
            observe_side=self.observe_side,
        )
        selected_entities = [
            entity
            for entity in contract.entities
            if entities is None or entity.name in entities
        ]
        for entity in selected_entities:
            entity_results = self._run_entity(entity, selected, continue_on_fail)
            report.results.extend(entity_results)
            if not continue_on_fail and any(
                item.status in {CheckStatus.FAIL, CheckStatus.ERROR} for item in entity_results
            ):
                break
        report.finished_at = datetime.now(UTC).isoformat()
        return report

    def _run_entity(self, entity: Entity, levels: list[str], continue_on_fail: bool) -> list[CheckResult]:
        if self.mode == "observe":
            return self._run_entity_observe(entity, levels, continue_on_fail)
        return self._run_entity_prove(entity, levels, continue_on_fail)

    def _run_entity_prove(
        self, entity: Entity, levels: list[str], continue_on_fail: bool
    ) -> list[CheckResult]:
        assert self.source is not None and self.target is not None
        results: list[CheckResult] = []
        source_info = None
        target_info = None
        try:
            source_info = inspect_relations(self.source, source_relations(entity))
            target_info = inspect_relations(self.target, target_relations(entity))
        except Exception:
            source_info = target_info = None

        runners: dict[str, list] = {
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
            batch = runners[level]()
            results.extend(batch)
            if not continue_on_fail and any(
                item.status in {CheckStatus.FAIL, CheckStatus.ERROR} for item in batch
            ):
                break
        return results

    def _run_entity_observe(
        self, entity: Entity, levels: list[str], continue_on_fail: bool
    ) -> list[CheckResult]:
        assert self.observe_side is not None
        results: list[CheckResult] = []
        live = self.source if self.observe_side == "source" else self.target
        assert live is not None
        missing = "target" if self.observe_side == "source" else "source"

        if "observe" in levels:
            batch = [export_observation(entity, live, self.observe_side, self.output_dir)]
            results.extend(batch)
            if not continue_on_fail and any(
                item.status in {CheckStatus.FAIL, CheckStatus.ERROR} for item in batch
            ):
                return results

        titles = {
            "1": "Schema (skipped — observe)",
            "probes": "Failure-mode probes (skipped — observe)",
            "2": "Volume (skipped — observe)",
            "3": "Metrics (skipped — observe)",
            "4": "Transforms (skipped — observe)",
            "hash": "Row hashes (skipped — observe)",
        }
        for level in LEVEL_ORDER:
            if level not in levels or level in {"observe", "5"}:
                continue
            if level in PAIRWISE_LEVELS:
                results.append(skip_pairwise(entity, level, titles[level], missing))

        if "5" in levels:
            if self.observe_side == "target" and len(target_relations(entity)) == 1:
                results.extend(check_invariants(entity, live))
            else:
                results.append(
                    CheckResult(
                        id=f"5.{entity.name}.observe_skip",
                        level="5",
                        title="Invariants (skipped — observe)",
                        entity=entity.name,
                        concern=Concern.reconciliation,
                        status=CheckStatus.SKIP,
                        message=(
                            "observe mode: invariants need observe_side=target "
                            "and a single target relation"
                        ),
                    )
                )
        return results
