from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from migrate_prove.connectors import Connector
from migrate_prove.models import (
    CheckResult,
    CheckStatus,
    Contract,
    Entity,
    RiskLevel,
    SuiteCheck,
)
from migrate_prove.progress import ProgressReporter
from migrate_prove.suite_checks import run_check, run_row_smoke, run_schema_check


LEVEL_TITLES = {
    "auto": "Automatic schema / row smoke",
    "schema": "Schema",
    "count": "Count",
    "group_count": "Group count",
    "sum": "Sum",
    "group_sum": "Group sum",
    "transforms": "Transforms",
    "row_sample": "Row sample",
    "hash": "Row hash",
    "probes": "Failure-mode probes",
    "invariant": "Invariant",
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
    def warned(self) -> int:
        return sum(1 for item in self.results if item.status == CheckStatus.WARN)

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

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "summary": {
                "total": self.total,
                "passed": self.passed,
                "failed": self.failed,
                "warned": self.warned,
                "skipped": self.skipped,
                "pass_rate": round(self.passed / self.total, 4) if self.total else 0,
            },
            "results": [item.model_dump() for item in self.results],
        }


class ValidationEngine:
    def __init__(
        self,
        source: Connector | None,
        target: Connector | None,
        *,
        output_dir: Path | None = None,
        progress: ProgressReporter | None = None,
        schema_check: bool = True,
        row_smoke_n: int | None = 5,
        params: dict[str, Any] | None = None,
    ) -> None:
        if source is None and target is None:
            raise ValueError("ValidationEngine requires at least one of source or target")
        self.source = source
        self.target = target
        self.output_dir = output_dir or Path("artifacts")
        self.progress = progress
        self.schema_check = schema_check
        self.row_smoke_n = row_smoke_n
        self.params = params or {}

    def run(
        self,
        contract: Contract,
        *,
        checks: list[SuiteCheck] | None = None,
        continue_on_fail: bool = True,
        entities: list[str] | None = None,
    ) -> RunReport:
        report = RunReport(
            name=contract.name,
            started_at=datetime.now(UTC).isoformat(),
        )
        selected_checks = list(checks or [])
        if entities is not None:
            allow = set(entities)
            selected_checks = [c for c in selected_checks if c.entity in allow]

        entity_names = sorted(
            {
                *(e.name for e in contract.entities if entities is None or e.name in entities),
                *(c.entity for c in selected_checks),
            }
        )
        preamble_entities = [
            contract.entity_by_name(name)
            for name in entity_names
            if any(e.name == name for e in contract.entities)
        ]

        steps: list[tuple[str, str, str, Any]] = []
        if self.schema_check:
            for entity in preamble_entities:
                steps.append(("auto.schema", entity.name, "Automatic schema", entity))
                if self.row_smoke_n is not None:
                    steps.append(("auto.smoke", entity.name, "Row smoke", entity))
        for check in selected_checks:
            steps.append(("check", check.entity, check.id, check))

        total = len(steps)
        if self.progress is not None:
            self.progress.begin(total)

        step_index = 0
        for kind, entity_name, label, payload in steps:
            step_index += 1
            if self.progress is not None:
                self.progress.step_start(
                    step_index,
                    total,
                    entity=entity_name,
                    level=kind,
                    label=label,
                )
            try:
                if kind == "auto.schema":
                    batch = run_schema_check(
                        payload,
                        self.source,
                        self.target,
                        check_id=f"auto.schema.{entity_name}",
                        hard=False,
                    )
                elif kind == "auto.smoke":
                    batch = run_row_smoke(
                        payload,
                        self.source,
                        self.target,
                        n=self.row_smoke_n or 5,
                    )
                else:
                    check = payload
                    assert isinstance(check, SuiteCheck)
                    entity = contract.entity_by_name(check.entity)
                    batch = run_check(check, entity, self.source, self.target, self.params)
            except Exception as exc:
                if self.progress is not None:
                    self.progress.step_error(
                        step_index,
                        total,
                        entity=entity_name,
                        level=kind,
                        label=label,
                        error=exc,
                    )
                raise
            if self.progress is not None:
                self.progress.step_finish(step_index, total, batch)
            report.results.extend(batch)
            if not continue_on_fail and any(
                item.status in {CheckStatus.FAIL, CheckStatus.ERROR} for item in batch
            ):
                break

        report.finished_at = datetime.now(UTC).isoformat()
        return report
