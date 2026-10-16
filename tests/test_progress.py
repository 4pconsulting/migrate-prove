from pathlib import Path

from migrate_prove.connectors import SqlAlchemyConnector
from migrate_prove.contract import load_contract
from migrate_prove.demo.banking import seed_banking
from migrate_prove.engine import ValidationEngine
from migrate_prove.models import CheckResult, CheckStatus, Concern


class RecordingProgress:
    def __init__(self) -> None:
        self.begin_total: int | None = None
        self.starts: list[tuple[int, int, str, str]] = []
        self.finishes: list[tuple[int, int, list[CheckStatus]]] = []
        self.errors: list[str] = []

    def begin(self, total_steps: int) -> None:
        self.begin_total = total_steps

    def step_start(
        self,
        current: int,
        total: int,
        *,
        entity: str,
        level: str,
        label: str,
    ) -> None:
        self.starts.append((current, total, entity, level))

    def step_finish(self, current: int, total: int, results: list[CheckResult]) -> None:
        self.finishes.append((current, total, [item.status for item in results]))

    def step_error(
        self,
        current: int,
        total: int,
        *,
        entity: str,
        level: str,
        label: str,
        error: BaseException,
    ) -> None:
        self.errors.append(f"{entity}:{level}:{error}")


def test_engine_emits_start_and_finish_for_each_level(tmp_path: Path):
    seed_banking(tmp_path)
    contract = load_contract(Path("examples/banking_demo/stm.yaml"))
    progress = RecordingProgress()
    engine = ValidationEngine(
        SqlAlchemyConnector(f"sqlite:///{(tmp_path / 'source.db').as_posix()}", "source"),
        SqlAlchemyConnector(f"sqlite:///{(tmp_path / 'target.db').as_posix()}", "target"),
        progress=progress,
    )
    report = engine.run(contract, levels=["1", "2"])

    assert progress.begin_total == len(progress.starts) == len(progress.finishes)
    assert progress.begin_total == len(contract.entities) * 2
    assert progress.starts[0] == (1, progress.begin_total, contract.entities[0].name, "1")
    assert all(current == finish_current for (current, *_), (finish_current, *_) in zip(progress.starts, progress.finishes))
    assert all(statuses for _, _, statuses in progress.finishes)
    assert report.total == sum(len(statuses) for _, _, statuses in progress.finishes)
    assert not progress.errors


def test_console_progress_prints_percent(capsys):
    from migrate_prove.progress import ConsoleProgress

    progress = ConsoleProgress()
    progress.begin(2)
    progress.step_start(1, 2, entity="customers", level="1", label="Level 1 — Structural & schema")
    progress.step_finish(
        1,
        2,
        [
            CheckResult(
                id="L1.customers.schema",
                level="1",
                title="Schema objects exist",
                entity="customers",
                concern=Concern.migration,
                status=CheckStatus.PASS,
                message="ok",
            )
        ],
    )
    progress.step_start(2, 2, entity="customers", level="2", label="Level 2 — Volume & cardinality")
    progress.step_finish(
        2,
        2,
        [
            CheckResult(
                id="L2.customers.volume.total",
                level="2",
                title="Total row count",
                entity="customers",
                concern=Concern.migration,
                status=CheckStatus.FAIL,
                message="totals differ",
            )
        ],
    )
    out = capsys.readouterr().out
    assert "starting" in out
    assert "0%" in out or "  0%" in out
    assert "50%" in out or " 50%" in out
    assert "100%" in out
    assert "PASS" in out
    assert "FAIL" in out
