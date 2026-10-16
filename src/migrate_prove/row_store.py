"""Disk-backed NDJSON row store for API connector pagination."""

from __future__ import annotations

import json
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

DEFAULT_BUFFER_ROWS = 5_000


class SpilledRowStore:
    """Buffer rows in memory, flush NDJSON chunks to disk, stream them back."""

    def __init__(
        self,
        *,
        buffer_rows: int = DEFAULT_BUFFER_ROWS,
        spill_dir: str | Path | None = None,
        cleanup: bool = True,
        max_rows: int | None = None,
    ) -> None:
        if buffer_rows < 1:
            raise ValueError("buffer_rows must be >= 1")
        self.buffer_rows = buffer_rows
        self.max_rows = max_rows
        self._cleanup = cleanup
        self._owned_dir = spill_dir is None
        if spill_dir is None:
            self.spill_dir = Path(tempfile.mkdtemp(prefix="migrate-prove-spill-"))
        else:
            self.spill_dir = Path(spill_dir)
            self.spill_dir.mkdir(parents=True, exist_ok=True)
        self._buffer: list[dict[str, Any]] = []
        self._chunk_index = 0
        self._total = 0
        self._closed = False

    @property
    def total_rows(self) -> int:
        return self._total

    @property
    def chunk_paths(self) -> list[Path]:
        return sorted(self.spill_dir.glob("chunk-*.ndjson"))

    def append(self, row: dict[str, Any]) -> None:
        if self._closed:
            raise RuntimeError("SpilledRowStore is closed")
        if self.max_rows is not None and self._total >= self.max_rows:
            raise ValueError(
                f"Row limit exceeded: max_rows={self.max_rows} "
                f"(set a higher max_rows or narrow filters)"
            )
        self._buffer.append(row)
        self._total += 1
        if len(self._buffer) >= self.buffer_rows:
            self.flush()

    def extend(self, rows: list[dict[str, Any]]) -> None:
        for row in rows:
            self.append(row)

    def flush(self) -> None:
        if not self._buffer:
            return
        path = self.spill_dir / f"chunk-{self._chunk_index:05d}.ndjson"
        with path.open("w", encoding="utf-8") as handle:
            for row in self._buffer:
                handle.write(json.dumps(row, default=str, separators=(",", ":")))
                handle.write("\n")
        self._chunk_index += 1
        self._buffer.clear()

    def close(self) -> None:
        if self._closed:
            return
        self.flush()
        self._closed = True

    def cleanup(self) -> None:
        self.close()
        if self._cleanup and self._owned_dir and self.spill_dir.exists():
            shutil.rmtree(self.spill_dir, ignore_errors=True)

    def __enter__(self) -> SpilledRowStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.cleanup()

    def iter_rows(self) -> Iterator[dict[str, Any]]:
        self.flush()
        for path in self.chunk_paths:
            with path.open(encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if line:
                        yield json.loads(line)

    def list_rows(self) -> list[dict[str, Any]]:
        return list(self.iter_rows())
