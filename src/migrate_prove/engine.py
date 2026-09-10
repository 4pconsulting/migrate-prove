from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

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
from migrate_prove.models import CheckResult, CheckStatus, Contract, Entity, RiskLevel


LEVEL_ORDER = ["1", "probes", "2", "3", "4", "hash", "5"]

LEVEL_TITLES = {
    "1": "Level 1 — Structural & schema",
    "probes": "Failure-mode probes",
    "2": "Level 2 — Volume & cardinality",
    "3": "Level 3 — Metric & financial reconciliation",
    "4": "Level 4 — Transformation rules",
    "hash": "Row-level cryptographic reconciliation",
    "5": "Level 5 — Business invariants",
}


@dataclass
class RunReport:
    name: str
    started_at: str
    finished_at: str = ""
    results: list[CheckResult] = field(default_factory=list)

    @property
    def passed(self) -> int:
        return sum(1 for item in self.results if item.status == CheckStatus.PASS)

    @property
    def failed(self) -> int:
        return sum(1 for item in self.results if item.status in {CheckStatus.FAIL, CheckStatus.ERROR})

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

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "summary": {
                "total": self.total,
                "passed": self.passed,
                "failed": self.failed,
                "pass_rate": round(self.passed / self.total, 4) if self.total else 0,
            },
            "results": [item.model_dump() for item in self.results],
        }


class ValidationEngine:
    def __init__(self, source: Connector, target: Connector) -> None:
        self.source = source
        self.target = target

    def run(
        self,
        contract: Contract,
        *,
        levels: list[str] | None = None,
        continue_on_fail: bool = True,
        entities: list[str] | None = None,
    ) -> RunReport:
        selected = levels or list(LEVEL_ORDER)
        report = RunReport(name=contract.name, started_at=datetime.now(UTC).isoformat())
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
        results: list[CheckResult] = []
        source_info = None
        target_info = None
        try:
            source_info = self.source.inspect_table(entity.source_table, entity.source_schema)
            target_info = self.target.inspect_table(entity.target_table, entity.target_schema)
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
            if level not in levels:
                continue
            batch = runners[level]()
            results.extend(batch)
            if not continue_on_fail and any(
                item.status in {CheckStatus.FAIL, CheckStatus.ERROR} for item in batch
            ):
                break
        return results
