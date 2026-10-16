"""Live console progress while a suite run executes."""

from __future__ import annotations

from typing import Protocol

from migrate_prove.models import CheckResult, CheckStatus


class ProgressReporter(Protocol):
    def begin(self, total_steps: int) -> None: ...

    def step_start(
        self,
        current: int,
        total: int,
        *,
        entity: str,
        level: str,
        label: str,
    ) -> None: ...

    def step_finish(self, current: int, total: int, results: list[CheckResult]) -> None: ...

    def step_error(
        self,
        current: int,
        total: int,
        *,
        entity: str,
        level: str,
        label: str,
        error: BaseException,
    ) -> None: ...


def _pct(completed: int, total: int) -> int:
    if total <= 0:
        return 100
    return min(100, int(round(100 * completed / total)))


def _prefix(current: int, total: int, completed: int) -> str:
    return f"[{current}/{total} · {_pct(completed, total):3d}%]"


_STATUS_STYLE = {
    CheckStatus.PASS: "green",
    CheckStatus.FAIL: "red",
    CheckStatus.ERROR: "red",
    CheckStatus.WARN: "yellow",
    CheckStatus.SKIP: "dim",
}

_STATUS_MARK = {
    CheckStatus.PASS: "✓",
    CheckStatus.FAIL: "✗",
    CheckStatus.ERROR: "✗",
    CheckStatus.WARN: "!",
    CheckStatus.SKIP: "·",
}


class ConsoleProgress:
    """Print start/finish lines for each check group so long runs do not look hung."""

    def __init__(self) -> None:
        from rich.console import Console

        self._console = Console()

    def begin(self, total_steps: int) -> None:
        self._console.print(f"[dim]Progress: {total_steps} check group(s)[/]")

    def step_start(
        self,
        current: int,
        total: int,
        *,
        entity: str,
        level: str,
        label: str,
    ) -> None:
        del level  # reserved for structured reporters
        prefix = _prefix(current, total, current - 1)
        self._console.print(
            f"[cyan]→[/] {prefix} [bold]{entity}[/] · {label} — starting"
        )

    def step_finish(self, current: int, total: int, results: list[CheckResult]) -> None:
        prefix = _prefix(current, total, current)
        if not results:
            self._console.print(f"[dim]·[/] {prefix} (no checks)")
            return
        for item in results:
            style = _STATUS_STYLE.get(item.status, "white")
            mark = _STATUS_MARK.get(item.status, "·")
            message = f" — {item.message}" if item.message else ""
            self._console.print(
                f"[{style}]{mark}[/] {prefix} {item.title} — "
                f"[{style}]{item.status.value}[/]{message}"
            )

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
        del level
        prefix = _prefix(current, total, current)
        self._console.print(
            f"[red]✗[/] {prefix} [bold]{entity}[/] · {label} — "
            f"[red]ERROR[/] — {error}"
        )
