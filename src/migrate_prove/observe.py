"""One-sided observation export for manual reconciliation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from migrate_prove.connectors import Connector
from migrate_prove.models import CheckResult, CheckStatus, Concern, Entity
from migrate_prove.sql import fetch_concat, source_relations, target_relations


def export_observation(
    entity: Entity,
    connector: Connector,
    side: Literal["source", "target"],
    output_dir: Path,
) -> CheckResult:
    """Fetch the live side and write NDJSON + JSON sidecar under output_dir/observe/."""
    refs = source_relations(entity) if side == "source" else target_relations(entity)
    where = entity.filter_source if side == "source" else entity.filter_target
    observe_dir = output_dir / "observe"
    observe_dir.mkdir(parents=True, exist_ok=True)
    ndjson_path = observe_dir / f"{entity.name}.{side}.ndjson"
    meta_path = observe_dir / f"{entity.name}.{side}.meta.json"

    try:
        rows = fetch_concat(connector, refs, where)
        columns: list[str] = []
        seen: set[str] = set()
        for row in rows:
            for key in row:
                if key not in seen:
                    seen.add(key)
                    columns.append(key)
        with ndjson_path.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, default=str, separators=(",", ":")))
                handle.write("\n")
        meta: dict[str, Any] = {
            "entity": entity.name,
            "side": side,
            "row_count": len(rows),
            "columns": columns,
            "filter": where,
            "parts": len(refs),
            "ndjson": ndjson_path.as_posix(),
        }
        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        return CheckResult(
            id=f"observe.{entity.name}.{side}",
            level="observe",
            title=f"Observe {side} export",
            entity=entity.name,
            concern=Concern.reconciliation,
            status=CheckStatus.PASS,
            expected=None,
            actual=len(rows),
            message=f"Wrote {len(rows)} row(s) to {ndjson_path.name}",
            evidence={"path": ndjson_path.as_posix(), "meta": meta_path.as_posix(), **meta},
        )
    except Exception as exc:
        return CheckResult(
            id=f"observe.{entity.name}.{side}",
            level="observe",
            title=f"Observe {side} export",
            entity=entity.name,
            concern=Concern.reconciliation,
            status=CheckStatus.ERROR,
            message=str(exc),
        )


def skip_pairwise(entity: Entity, level: str, title: str, missing_side: str) -> CheckResult:
    return CheckResult(
        id=f"{level}.{entity.name}.observe_skip",
        level=level,
        title=title,
        entity=entity.name,
        concern=Concern.reconciliation,
        status=CheckStatus.SKIP,
        message=f"observe mode: {missing_side} side not connected",
    )
