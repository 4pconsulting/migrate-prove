"""Execute suite-declared prove checks against an STM contract."""

from __future__ import annotations

import random
from collections import defaultdict
from decimal import Decimal
from typing import Any

from migrate_prove.connectors import Connector, TableInfo
from migrate_prove.hashing import row_signature
from migrate_prove.models import (
    CheckResult,
    CheckStatus,
    Concern,
    DimensionSpec,
    Entity,
    InvariantSpec,
    MappingKind,
    SuiteCheck,
)
from migrate_prove.normalize import canonicalize
from migrate_prove.rules import apply_mapping
from migrate_prove.secrets import expand_string
from migrate_prove.sql import (
    aggregate_concat,
    aggregate_grouped_concat,
    count_concat,
    fetch_concat,
    group_count_concat,
    inspect_relations,
    mapped_dimension_sql,
    missing_columns_on_parts,
    source_relations,
    target_relations,
)


def _slice_label(key: tuple) -> str:
    return "|".join("" if part is None else str(part) for part in key)


def _evidence_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _evidence_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_evidence_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, Decimal):
        return str(value)
    return str(value)


def _sample_one(items: list[Any], *, prefer=None) -> list[Any]:
    if not items:
        return []
    if prefer is not None:
        for item in items:
            if prefer(item):
                return [_evidence_value(item)]
    return [_evidence_value(items[0])]


def _ok(
    *,
    id: str,
    level: str,
    title: str,
    entity: str,
    concern: Concern,
    status: CheckStatus,
    message: str = "",
    expected: Any = None,
    actual: Any = None,
    variance: Any = None,
    evidence: dict | None = None,
) -> CheckResult:
    return CheckResult(
        id=id,
        level=level,
        title=title,
        entity=entity,
        concern=concern,
        status=status,
        expected=expected,
        actual=actual,
        variance=variance,
        message=message,
        evidence=evidence or {},
    )


def _index_by_key(rows: list[dict[str, Any]], columns: list[str]) -> dict[tuple, dict[str, Any]]:
    indexed: dict[tuple, dict[str, Any]] = {}
    for row in rows:
        indexed[tuple(row[col] for col in columns)] = row
    return indexed


def _within_tolerance(expected: float, actual: float, abs_tol: float, pct_tol: float | None) -> bool:
    delta = abs(actual - expected)
    if delta <= abs_tol:
        return True
    if pct_tol is not None and expected != 0 and (delta / abs(expected)) * 100 <= pct_tol:
        return True
    return False


def _is_api_dialect(connector: Connector) -> bool:
    return connector.dialect_name() in {"salesforce", "http"}


def expand_params(text: str | None, params: dict[str, Any]) -> str | None:
    if text is None:
        return None
    environ = {str(k): str(v) for k, v in params.items()}
    return expand_string(text, environ)


def filters_for(
    check: SuiteCheck, params: dict[str, Any]
) -> tuple[str | None, str | None]:
    if check.filter is None:
        return None, None
    return (
        expand_params(check.filter.source, params),
        expand_params(check.filter.target, params),
    )


def resolve_sum_exprs(
    entity: Entity, check: SuiteCheck
) -> tuple[str, str]:
    if check.source_expr and check.target_expr:
        return check.source_expr, check.target_expr
    if not check.field:
        raise ValueError(f"Check {check.id}: sum/group_sum requires field or source_expr/target_expr")
    mapping = entity.mapping_for_source(check.field)
    if mapping is None:
        raise ValueError(
            f"Check {check.id}: source field {check.field!r} not found in STM mappings"
        )
    source_expr = check.source_expr or f"SUM({mapping.source})"
    target_expr = check.target_expr or f"SUM({mapping.target})"
    return source_expr, target_expr


def resolve_dimension_bindings(
    entity: Entity, dims: list[DimensionSpec], source: Connector | None
) -> tuple[list[str], dict[str, str] | None, list[str], dict[str, Any]]:
    """Return source dim names, source dim SQL, target dim names, and evidence bindings."""
    source_dims: list[str] = []
    target_dims: list[str] = []
    source_dim_sql: dict[str, str] = {}
    bindings: list[dict[str, Any]] = []
    for dim in dims:
        field = dim.field or ""
        binding: dict[str, Any] = {"field": field, "side": dim.side}
        if dim.side == "target":
            target_name = dim.target or field
            target_dims.append(target_name)
            binding["target"] = target_name
        elif dim.side == "source":
            source_name = dim.source or field
            source_dims.append(source_name)
            binding["source"] = source_name
        else:
            if dim.source and dim.target:
                source_name, target_name = dim.source, dim.target
            else:
                mapping = entity.mapping_for_target(field) or entity.mapping_for_source(field)
                if mapping is None or not mapping.source:
                    raise ValueError(
                        f"Dimension {field!r} is not mapped; use side/source/target explicitly"
                    )
                source_name, target_name = mapping.source, mapping.target
            if source is not None:
                try:
                    source_dim_sql[target_name] = mapped_dimension_sql(
                        source, entity, target_name
                    )
                except Exception:
                    source_dim_sql[target_name] = source_name
            # Shared group keys use target names; source SQL may CASE-map into them.
            source_dims.append(target_name)
            target_dims.append(target_name)
            binding["source"] = source_name
            binding["target"] = target_name
            if target_name in source_dim_sql:
                binding["source_sql"] = source_dim_sql[target_name]
        bindings.append(binding)
    return source_dims, (source_dim_sql or None), target_dims, {"dimensions": bindings}


def run_schema_check(
    entity: Entity,
    source: Connector | None,
    target: Connector | None,
    *,
    check_id: str,
    hard: bool,
) -> list[CheckResult]:
    """Catalog vs STM. hard=False → WARN on drift; hard=True → FAIL."""
    bad = CheckStatus.FAIL if hard else CheckStatus.WARN
    level = "schema" if hard else "auto"
    results: list[CheckResult] = []

    source_info: TableInfo | None = None
    target_info: TableInfo | None = None
    try:
        if source is not None:
            source_info = inspect_relations(source, source_relations(entity))
        if target is not None:
            target_info = inspect_relations(target, target_relations(entity))
    except Exception as exc:
        return [
            _ok(
                id=check_id,
                level=level,
                title="Schema inspect",
                entity=entity.name,
                concern=Concern.migration,
                status=CheckStatus.ERROR,
                message=str(exc),
            )
        ]

    missing_source: list[str] = []
    missing_target: list[str] = []
    if source is not None and source_info is not None:
        required_source = [m.source for m in entity.mappings if m.source]
        gaps = missing_columns_on_parts(source, source_relations(entity), required_source)
        missing_source = sorted(
            {col for cols in gaps.values() for col in cols}
            | {
                m.source
                for m in entity.mappings
                if m.source and m.source not in source_info.columns
            }
        )
    if target is not None and target_info is not None:
        required_target = [m.target for m in entity.mappings]
        gaps = missing_columns_on_parts(target, target_relations(entity), required_target)
        missing_target = sorted(
            {col for cols in gaps.values() for col in cols}
            | {
                m.target
                for m in entity.mappings
                if m.target not in target_info.columns
            }
        )

    col_bad = bool(missing_source or missing_target)
    results.append(
        _ok(
            id=f"{check_id}.columns",
            level=level,
            title="Mapped columns exist",
            entity=entity.name,
            concern=Concern.migration,
            status=bad if col_bad else CheckStatus.PASS,
            expected={"source_missing": [], "target_missing": []},
            actual={"source_missing": missing_source, "target_missing": missing_target},
            evidence={
                "output": {"source_missing": missing_source, "target_missing": missing_target},
                "sample": _sample_one(
                    [
                        {"source_missing": missing_source, "target_missing": missing_target}
                    ]
                ),
            },
            message=(
                f"Missing columns source={missing_source} target={missing_target}"
                if col_bad
                else "All mapped columns are present"
            ),
        )
    )

    nullability_mismatches: list[str] = []
    length_notes: list[dict[str, Any]] = []
    if target_info is not None:
        for mapping in entity.mappings:
            column = target_info.columns.get(mapping.target)
            if column and not mapping.nullable and column.nullable:
                nullability_mismatches.append(mapping.target)
            if column and mapping.max_length is not None and column.max_length is not None:
                if column.max_length < mapping.max_length:
                    length_notes.append(
                        {
                            "field": mapping.target,
                            "contract_max_length": mapping.max_length,
                            "target_max_length": column.max_length,
                        }
                    )

    null_bad = bool(nullability_mismatches)
    results.append(
        _ok(
            id=f"{check_id}.nullability",
            level=level,
            title="Target nullability vs contract",
            entity=entity.name,
            concern=Concern.migration,
            status=bad if null_bad else CheckStatus.PASS,
            actual=nullability_mismatches,
            evidence={
                "output": {"mismatches": nullability_mismatches},
                "sample": _sample_one([{"field": f} for f in nullability_mismatches]),
            },
            message=(
                f"NOT NULL contract but target allows NULL: {nullability_mismatches}"
                if null_bad
                else "Nullability matches the contract"
            ),
        )
    )

    if length_notes:
        results.append(
            _ok(
                id=f"{check_id}.max_length",
                level=level,
                title="Target max_length vs contract",
                entity=entity.name,
                concern=Concern.migration,
                status=bad,
                actual=length_notes,
                evidence={"output": {"drifts": length_notes}, "sample": _sample_one(length_notes)},
                message=f"Target max_length narrower than contract: {length_notes}",
            )
        )
    elif target is not None:
        results.append(
            _ok(
                id=f"{check_id}.max_length",
                level=level,
                title="Target max_length vs contract",
                entity=entity.name,
                concern=Concern.migration,
                status=CheckStatus.PASS,
                message="No max_length contract drift detected",
                evidence={"output": {"drifts": []}},
            )
        )
    return results


def run_row_smoke(
    entity: Entity,
    source: Connector | None,
    target: Connector | None,
    *,
    n: int,
) -> list[CheckResult]:
    """Soft-check N random rows against mapping constraints → WARN."""
    if source is None or target is None:
        live = source or target
        side = "source" if source else "target"
        if live is None:
            return []
        refs = source_relations(entity) if side == "source" else target_relations(entity)
        rows = fetch_concat(live, refs, None)
        sample = random.sample(rows, min(n, len(rows))) if rows else []
        return [
            _ok(
                id=f"auto.row_smoke.{entity.name}",
                level="auto",
                title="Row smoke (one-sided)",
                entity=entity.name,
                concern=Concern.migration,
                status=CheckStatus.PASS,
                actual={"row_count": len(rows), "sampled": len(sample)},
                evidence={
                    "output": {"side": side, "sampled": len(sample)},
                    "sample": _sample_one(sample),
                },
                message=f"One-sided smoke: sampled {len(sample)} of {len(rows)} {side} row(s)",
            )
        ]

    source_rows = fetch_concat(source, source_relations(entity), None)
    target_rows = _index_by_key(
        fetch_concat(target, target_relations(entity), None),
        entity.business_key,
    )
    pairs = []
    for source_row in source_rows:
        mapped_key = tuple(
            apply_mapping(source_row, entity.mapping_for_target(col))  # type: ignore[arg-type]
            for col in entity.business_key
        )
        target_row = target_rows.get(mapped_key)
        if target_row is not None:
            pairs.append((source_row, target_row, mapped_key))
    sample_pairs = random.sample(pairs, min(n, len(pairs))) if pairs else []
    hits: list[dict[str, Any]] = []
    for source_row, target_row, key in sample_pairs:
        for mapping in entity.mappings:
            if mapping.kind == MappingKind.surrogate:
                continue
            actual = target_row.get(mapping.target)
            if not mapping.nullable and actual is None:
                hits.append(
                    {
                        "key": dict(zip(entity.business_key, key, strict=True)),
                        "field": mapping.target,
                        "issue": "null_not_allowed",
                    }
                )
            if mapping.max_length and isinstance(actual, str) and len(actual) > mapping.max_length:
                hits.append(
                    {
                        "key": dict(zip(entity.business_key, key, strict=True)),
                        "field": mapping.target,
                        "issue": "exceeds_max_length",
                        "length": len(actual),
                        "max_length": mapping.max_length,
                    }
                )
    return [
        _ok(
            id=f"auto.row_smoke.{entity.name}",
            level="auto",
            title="Row smoke vs mapping constraints",
            entity=entity.name,
            concern=Concern.migration,
            status=CheckStatus.WARN if hits else CheckStatus.PASS,
            actual=len(hits),
            evidence={
                "output": {"sampled": len(sample_pairs), "issues": len(hits)},
                "sample": _sample_one(hits) if hits else _sample_one(
                    [
                        {
                            "key": dict(zip(entity.business_key, key, strict=True)),
                            "ok": True,
                        }
                        for _, _, key in sample_pairs[:1]
                    ]
                ),
            },
            message=(
                f"{len(hits)} constraint issue(s) in {len(sample_pairs)} smoked row(s)"
                if hits
                else f"No constraint issues in {len(sample_pairs)} smoked row(s)"
            ),
        )
    ]


def run_count(
    check: SuiteCheck,
    entity: Entity,
    source: Connector | None,
    target: Connector | None,
    params: dict[str, Any],
) -> list[CheckResult]:
    src_f, tgt_f = filters_for(check, params)
    src_n = count_concat(source, source_relations(entity), src_f) if source else None
    tgt_n = count_concat(target, target_relations(entity), tgt_f) if target else None
    if source is None or target is None:
        live = src_n if source else tgt_n
        side = "source" if source else "target"
        return [
            _ok(
                id=check.id,
                level="count",
                title="Row count",
                entity=entity.name,
                concern=Concern.migration,
                status=CheckStatus.PASS,
                actual=live,
                evidence={
                    "output": {"side": side, "count": live, "filter": {"source": src_f, "target": tgt_f}},
                    "sample": [{"side": side, "count": live}],
                },
                message=f"COUNT(*) {side}={live} (one-sided emit)",
            )
        ]
    matched = src_n == tgt_n
    return [
        _ok(
            id=check.id,
            level="count",
            title="Row count",
            entity=entity.name,
            concern=Concern.migration,
            status=CheckStatus.PASS if matched else CheckStatus.FAIL,
            expected=src_n,
            actual=tgt_n,
            variance=(tgt_n or 0) - (src_n or 0),
            evidence={
                "output": {
                    "source": src_n,
                    "target": tgt_n,
                    "filter": {"source": src_f, "target": tgt_f},
                },
                "sample": [{"source": src_n, "target": tgt_n, "variance": (tgt_n or 0) - (src_n or 0)}],
            },
            message=f"COUNT(*) source={src_n}, target={tgt_n}"
            + (" — match" if matched else " — differ"),
        )
    ]


def run_group_count(
    check: SuiteCheck,
    entity: Entity,
    source: Connector | None,
    target: Connector | None,
    params: dict[str, Any],
) -> list[CheckResult]:
    src_f, tgt_f = filters_for(check, params)
    dims = check.resolved_dimensions()
    if not dims:
        raise ValueError(f"Check {check.id}: group_count requires dimensions")
    source_dims, source_dim_sql, target_dims, dim_evidence = resolve_dimension_bindings(
        entity, dims, source
    )
    filter_ev = {"source": src_f, "target": tgt_f}

    if source is None or target is None:
        live = source or target
        assert live is not None
        side = "source" if source else "target"
        use_dims = source_dims if side == "source" else target_dims
        dim_sql = source_dim_sql if side == "source" else None
        where = src_f if side == "source" else tgt_f
        refs = source_relations(entity) if side == "source" else target_relations(entity)
        if not use_dims:
            return [
                _ok(
                    id=check.id,
                    level="group_count",
                    title="Group count",
                    entity=entity.name,
                    concern=Concern.migration,
                    status=CheckStatus.SKIP,
                    message=f"No {side}-side dimensions to group",
                    evidence={"output": {**dim_evidence, "filter": filter_ev}},
                )
            ]
        groups = group_count_concat(live, refs, use_dims, where, dim_sql)
        sample = _sample_one(groups)
        return [
            _ok(
                id=check.id,
                level="group_count",
                title="Group count",
                entity=entity.name,
                concern=Concern.migration,
                status=CheckStatus.PASS,
                actual={_slice_label(tuple(row.get(d) for d in use_dims)): int(row["row_count"]) for row in groups},
                evidence={
                    "output": {"side": side, "groups": len(groups), **dim_evidence, "filter": filter_ev},
                    "slices": [
                        {"slice": {d: row.get(d) for d in use_dims}, "count": int(row["row_count"])}
                        for row in groups
                    ],
                    "sample": sample,
                },
                message=f"GROUP BY {use_dims}: {len(groups)} group(s) on {side}; sample={sample[0] if sample else None}",
            )
        ]

    # Both sides — only compare dimensions present on both (side: both).
    both_dims = [d for d in dims if d.side == "both"]
    if not both_dims:
        return [
            _ok(
                id=check.id,
                level="group_count",
                title="Group count",
                entity=entity.name,
                concern=Concern.migration,
                status=CheckStatus.SKIP,
                message="No shared (side: both) dimensions to compare",
                evidence={"output": {**dim_evidence, "filter": filter_ev}},
            )
        ]
    source_dims, source_dim_sql, target_dims, _shared = resolve_dimension_bindings(
        entity, both_dims, source
    )
    source_groups = group_count_concat(
        source, source_relations(entity), source_dims, src_f, source_dim_sql
    )
    target_groups = group_count_concat(
        target, target_relations(entity), target_dims, tgt_f, None
    )
    source_map = {
        tuple(row[dim] for dim in source_dims): int(row["row_count"]) for row in source_groups
    }
    target_map = {
        tuple(row[dim] for dim in target_dims): int(row["row_count"]) for row in target_groups
    }
    keys = sorted(set(source_map) | set(target_map), key=str)
    slices = []
    failed = False
    for key in keys:
        expected = source_map.get(key, 0)
        actual = target_map.get(key, 0)
        variance = actual - expected
        if variance != 0:
            failed = True
        slices.append(
            {
                "slice": dict(zip(target_dims, key, strict=True)),
                "source": expected,
                "target": actual,
                "variance": variance,
            }
        )
    sample = _sample_one(slices, prefer=lambda row: row.get("variance") not in (0, 0.0, None))
    return [
        _ok(
            id=check.id,
            level="group_count",
            title="Group count",
            entity=entity.name,
            concern=Concern.transformation if failed else Concern.migration,
            status=CheckStatus.FAIL if failed else CheckStatus.PASS,
            expected={_slice_label(k): source_map[k] for k in source_map},
            actual={_slice_label(k): target_map[k] for k in target_map},
            evidence={
                "output": {**dim_evidence, "filter": filter_ev, "groups": len(slices)},
                "slices": slices,
                "sample": sample,
            },
            message=(
                f"GROUP BY {target_dims}: {len(slices)} slice(s); sample={sample[0] if sample else None}"
                + (" — differ" if failed else " — match")
            ),
        )
    ]


def run_sum(
    check: SuiteCheck,
    entity: Entity,
    source: Connector | None,
    target: Connector | None,
    params: dict[str, Any],
) -> list[CheckResult]:
    src_f, tgt_f = filters_for(check, params)
    source_expr, target_expr = resolve_sum_exprs(entity, check)
    src_v = (
        float(aggregate_concat(source, source_relations(entity), source_expr, src_f) or 0)
        if source
        else None
    )
    tgt_v = (
        float(aggregate_concat(target, target_relations(entity), target_expr, tgt_f) or 0)
        if target
        else None
    )
    if source is None or target is None:
        live = src_v if source else tgt_v
        side = "source" if source else "target"
        expr = source_expr if source else target_expr
        return [
            _ok(
                id=check.id,
                level="sum",
                title="Sum",
                entity=entity.name,
                concern=Concern.reconciliation,
                status=CheckStatus.PASS,
                actual=live,
                evidence={
                    "output": {"side": side, "expr": expr, "value": live, "filter": {"source": src_f, "target": tgt_f}},
                    "sample": [{"side": side, "expr": expr, "value": live}],
                },
                message=f"{expr} {side}={live} (one-sided emit)",
            )
        ]
    ok = _within_tolerance(src_v or 0, tgt_v or 0, check.tolerance_abs, check.tolerance_pct)
    return [
        _ok(
            id=check.id,
            level="sum",
            title="Sum",
            entity=entity.name,
            concern=Concern.reconciliation,
            status=CheckStatus.PASS if ok else CheckStatus.FAIL,
            expected=src_v,
            actual=tgt_v,
            variance=(tgt_v or 0) - (src_v or 0),
            evidence={
                "output": {
                    "source": src_v,
                    "target": tgt_v,
                    "source_sql": source_expr,
                    "target_sql": target_expr,
                    "filter": {"source": src_f, "target": tgt_f},
                },
                "sample": [{"source": src_v, "target": tgt_v, "variance": (tgt_v or 0) - (src_v or 0)}],
            },
            message=f"{source_expr}={src_v}, {target_expr}={tgt_v}"
            + (" — within tolerance" if ok else " — outside tolerance"),
        )
    ]


def run_group_sum(
    check: SuiteCheck,
    entity: Entity,
    source: Connector | None,
    target: Connector | None,
    params: dict[str, Any],
) -> list[CheckResult]:
    src_f, tgt_f = filters_for(check, params)
    source_expr, target_expr = resolve_sum_exprs(entity, check)
    dims = [d for d in check.resolved_dimensions() if d.side == "both"]
    if not dims:
        dims = check.resolved_dimensions()
    if not dims:
        raise ValueError(f"Check {check.id}: group_sum requires dimensions")
    source_dims, source_dim_sql, target_dims, dim_evidence = resolve_dimension_bindings(
        entity, dims, source
    )
    filter_ev = {"source": src_f, "target": tgt_f}

    if source is None or target is None:
        live = source or target
        assert live is not None
        side = "source" if source else "target"
        expr = source_expr if side == "source" else target_expr
        use_dims = source_dims if side == "source" else target_dims
        where = src_f if side == "source" else tgt_f
        refs = source_relations(entity) if side == "source" else target_relations(entity)
        dim_sql = source_dim_sql if side == "source" else None
        rows = aggregate_grouped_concat(live, refs, expr, use_dims, where, dim_sql)
        sample = _sample_one(rows)
        return [
            _ok(
                id=check.id,
                level="group_sum",
                title="Group sum",
                entity=entity.name,
                concern=Concern.reconciliation,
                status=CheckStatus.PASS,
                actual={
                    _slice_label(tuple(row.get(d) for d in use_dims)): float(row["metric"] or 0)
                    for row in rows
                },
                evidence={
                    "output": {"side": side, **dim_evidence, "filter": filter_ev},
                    "slices": rows,
                    "sample": sample,
                },
                message=f"{expr} by {use_dims} on {side}: {len(rows)} group(s); sample={sample[0] if sample else None}",
            )
        ]

    source_rows = {
        tuple(row[dim] for dim in source_dims): float(row["metric"] or 0)
        for row in aggregate_grouped_concat(
            source, source_relations(entity), source_expr, source_dims, src_f, source_dim_sql
        )
    }
    target_rows = {
        tuple(row[dim] for dim in target_dims): float(row["metric"] or 0)
        for row in aggregate_grouped_concat(
            target, target_relations(entity), target_expr, target_dims, tgt_f, None
        )
    }
    keys = sorted(set(source_rows) | set(target_rows), key=str)
    slices = []
    failed = False
    for key in keys:
        expected = source_rows.get(key, 0.0)
        actual = target_rows.get(key, 0.0)
        ok = _within_tolerance(expected, actual, check.tolerance_abs, check.tolerance_pct)
        if not ok:
            failed = True
        slices.append(
            {
                "slice": dict(zip(target_dims, key, strict=True)),
                "source": expected,
                "target": actual,
                "variance": actual - expected,
            }
        )
    sample = _sample_one(
        slices,
        prefer=lambda row: not _within_tolerance(
            float(row["source"]), float(row["target"]), check.tolerance_abs, check.tolerance_pct
        ),
    )
    return [
        _ok(
            id=check.id,
            level="group_sum",
            title="Group sum",
            entity=entity.name,
            concern=Concern.reconciliation,
            status=CheckStatus.FAIL if failed else CheckStatus.PASS,
            expected={_slice_label(k): source_rows[k] for k in source_rows},
            actual={_slice_label(k): target_rows[k] for k in target_rows},
            evidence={
                "output": {
                    "source_sql": source_expr,
                    "target_sql": target_expr,
                    **dim_evidence,
                    "filter": filter_ev,
                },
                "slices": slices,
                "sample": sample,
            },
            message=(
                f"{source_expr} vs {target_expr} by {target_dims}; sample={sample[0] if sample else None}"
                + (" — mismatch" if failed else " — within tolerance")
            ),
        )
    ]


def _expected_row(source_row: dict[str, Any], entity: Entity) -> dict[str, Any]:
    expected: dict[str, Any] = {}
    for mapping in entity.mappings:
        if mapping.kind == MappingKind.surrogate:
            continue
        expected[mapping.target] = apply_mapping(source_row, mapping)
    return expected


def run_transforms(
    check: SuiteCheck,
    entity: Entity,
    source: Connector | None,
    target: Connector | None,
    params: dict[str, Any],
) -> list[CheckResult]:
    if source is None or target is None:
        live = source or target
        side = "source" if source else "target"
        assert live is not None
        src_f, tgt_f = filters_for(check, params)
        where = src_f if side == "source" else tgt_f
        refs = source_relations(entity) if side == "source" else target_relations(entity)
        rows = fetch_concat(live, refs, where)
        sample = _sample_one(rows)
        return [
            _ok(
                id=check.id,
                level="transforms",
                title="Transforms",
                entity=entity.name,
                concern=Concern.transformation,
                status=CheckStatus.PASS,
                actual=len(rows),
                evidence={"output": {"side": side, "rows": len(rows)}, "sample": sample},
                message=f"One-sided transform emit: {len(rows)} {side} row(s); sample={sample[0] if sample else None}",
            )
        ]

    src_f, tgt_f = filters_for(check, params)
    source_rows = fetch_concat(source, source_relations(entity), src_f)
    target_rows = _index_by_key(
        fetch_concat(target, target_relations(entity), tgt_f),
        entity.business_key,
    )
    field_filter = set(check.fields) if check.fields else None
    transform_fields = [
        mapping
        for mapping in entity.mappings
        if mapping.kind != MappingKind.surrogate
        and (
            mapping.kind in {MappingKind.transform, MappingKind.coerce}
            or mapping.lookup
            or mapping.rules
        )
    ]
    if not transform_fields:
        transform_fields = [m for m in entity.mappings if m.kind != MappingKind.surrogate]
    if field_filter is not None:
        transform_fields = [
            m
            for m in transform_fields
            if m.target in field_filter or (m.source and m.source in field_filter)
        ]

    mismatches: dict[str, list[dict[str, Any]]] = defaultdict(list)
    matches: dict[str, list[dict[str, Any]]] = defaultdict(list)
    evaluated = 0
    for source_row in source_rows:
        mapped_key = tuple(
            apply_mapping(source_row, entity.mapping_for_target(col))  # type: ignore[arg-type]
            for col in entity.business_key
        )
        target_row = target_rows.get(mapped_key)
        if target_row is None:
            continue
        evaluated += 1
        expected = _expected_row(source_row, entity)
        for mapping in transform_fields:
            actual = target_row.get(mapping.target)
            wanted = expected[mapping.target]
            sample_row = {
                "key": dict(zip(entity.business_key, mapped_key, strict=True)),
                "expected": wanted,
                "actual": actual,
            }
            if canonicalize(wanted, mapping.normalize) != canonicalize(actual, mapping.normalize):
                mismatches[mapping.target].append(sample_row)
            elif len(matches[mapping.target]) < 1:
                matches[mapping.target].append(sample_row)

    results: list[CheckResult] = []
    for mapping in transform_fields:
        misses = mismatches.get(mapping.target, [])
        sample = _sample_one(misses) if misses else _sample_one(matches.get(mapping.target, []))
        results.append(
            _ok(
                id=f"{check.id}.{mapping.target}",
                level="transforms",
                title=f"Transform {mapping.target}",
                entity=entity.name,
                concern=Concern.transformation,
                status=CheckStatus.FAIL if misses else CheckStatus.PASS,
                expected=0,
                actual=len(misses),
                variance=len(misses),
                evidence={"sample": sample, "evaluated": evaluated, "mismatch_count": len(misses)},
                message=(
                    f"{len(misses)}/{evaluated} row(s) mismatch; sample={sample[0] if sample else None}"
                    if misses
                    else f"{evaluated} row(s) match STM; sample={sample[0] if sample else None}"
                ),
            )
        )
    if not results:
        results.append(
            _ok(
                id=check.id,
                level="transforms",
                title="Transforms",
                entity=entity.name,
                concern=Concern.transformation,
                status=CheckStatus.SKIP,
                message="No transform fields selected",
            )
        )
    return results


def run_row_sample(
    check: SuiteCheck,
    entity: Entity,
    source: Connector | None,
    target: Connector | None,
    params: dict[str, Any],
) -> list[CheckResult]:
    if source is None or target is None:
        return [
            _ok(
                id=check.id,
                level="row_sample",
                title="Row sample",
                entity=entity.name,
                concern=Concern.transformation,
                status=CheckStatus.SKIP,
                message="row_sample requires both source and target",
            )
        ]
    fields = check.fields or [m.source for m in entity.mappings if m.source]
    src_f, tgt_f = filters_for(check, params)
    source_rows = fetch_concat(source, source_relations(entity), src_f)
    target_rows = _index_by_key(
        fetch_concat(target, target_relations(entity), tgt_f),
        entity.business_key,
    )
    pairs = []
    for source_row in source_rows:
        mapped_key = tuple(
            apply_mapping(source_row, entity.mapping_for_target(col))  # type: ignore[arg-type]
            for col in entity.business_key
        )
        target_row = target_rows.get(mapped_key)
        if target_row is not None:
            pairs.append((source_row, target_row, mapped_key))
    sample_pairs = random.sample(pairs, min(check.n, len(pairs))) if pairs else []
    misses: list[dict[str, Any]] = []
    ok_sample: list[dict[str, Any]] = []
    for source_row, target_row, key in sample_pairs:
        row_miss = False
        compared: dict[str, Any] = {"key": dict(zip(entity.business_key, key, strict=True))}
        for source_field in fields:
            mapping = entity.mapping_for_source(source_field)
            if mapping is None:
                continue
            wanted = apply_mapping(source_row, mapping)
            actual = target_row.get(mapping.target)
            compared[mapping.target] = {"expected": wanted, "actual": actual}
            if canonicalize(wanted, mapping.normalize) != canonicalize(actual, mapping.normalize):
                row_miss = True
        if row_miss:
            misses.append(compared)
        elif len(ok_sample) < 1:
            ok_sample.append(compared)
    sample = _sample_one(misses) if misses else _sample_one(ok_sample)
    return [
        _ok(
            id=check.id,
            level="row_sample",
            title="Row sample",
            entity=entity.name,
            concern=Concern.transformation,
            status=CheckStatus.FAIL if misses else CheckStatus.PASS,
            expected=0,
            actual=len(misses),
            evidence={
                "output": {"sampled": len(sample_pairs), "fields": fields, "mismatches": len(misses)},
                "sample": sample,
            },
            message=(
                f"{len(misses)}/{len(sample_pairs)} sampled row(s) mismatch; sample={sample[0] if sample else None}"
                if misses
                else f"{len(sample_pairs)} sampled row(s) match; sample={sample[0] if sample else None}"
            ),
        )
    ]


def run_hash(
    check: SuiteCheck,
    entity: Entity,
    source: Connector | None,
    target: Connector | None,
    params: dict[str, Any],
) -> list[CheckResult]:
    if source is None or target is None:
        return [
            _ok(
                id=check.id,
                level="hash",
                title="Row hash",
                entity=entity.name,
                concern=Concern.migration,
                status=CheckStatus.SKIP,
                message="hash requires both source and target",
            )
        ]
    hash_fields = [m for m in entity.mappings if m.in_hash and m.kind != MappingKind.surrogate]
    src_f, tgt_f = filters_for(check, params)
    source_rows = fetch_concat(source, source_relations(entity), src_f)
    target_rows = _index_by_key(
        fetch_concat(target, target_relations(entity), tgt_f),
        entity.business_key,
    )
    source_keys = set()
    missing: list[Any] = []
    hash_misses: list[dict[str, Any]] = []
    hash_matches: list[dict[str, Any]] = []
    for source_row in source_rows:
        mapped_key = tuple(
            apply_mapping(source_row, entity.mapping_for_target(col))  # type: ignore[arg-type]
            for col in entity.business_key
        )
        source_keys.add(mapped_key)
        target_row = target_rows.get(mapped_key)
        if target_row is None:
            missing.append(dict(zip(entity.business_key, mapped_key, strict=True)))
            continue
        expected_values = [apply_mapping(source_row, mapping) for mapping in hash_fields]
        actual_values = [target_row.get(mapping.target) for mapping in hash_fields]
        specs = [mapping.normalize for mapping in hash_fields]
        expected_hash = row_signature(expected_values, specs)
        actual_hash = row_signature(actual_values, specs)
        sample_row = {
            "key": dict(zip(entity.business_key, mapped_key, strict=True)),
            "source_hash": expected_hash,
            "target_hash": actual_hash,
        }
        if expected_hash != actual_hash:
            hash_misses.append(sample_row)
        elif len(hash_matches) < 1:
            hash_matches.append(sample_row)
    target_keys = set(target_rows)
    extras = [
        dict(zip(entity.business_key, key, strict=True))
        for key in sorted(target_keys - source_keys, key=str)
    ]
    return [
        _ok(
            id=f"{check.id}.missing",
            level="hash",
            title="Keys missing in target",
            entity=entity.name,
            concern=Concern.migration,
            status=CheckStatus.FAIL if missing else CheckStatus.PASS,
            expected=0,
            actual=len(missing),
            evidence={"sample": _sample_one(missing), "output": {"missing": len(missing)}},
            message=f"{len(missing)} missing key(s); sample={_sample_one(missing)[0] if missing else None}",
        ),
        _ok(
            id=f"{check.id}.extra",
            level="hash",
            title="Keys extra in target",
            entity=entity.name,
            concern=Concern.migration,
            status=CheckStatus.FAIL if extras else CheckStatus.PASS,
            expected=0,
            actual=len(extras),
            evidence={"sample": _sample_one(extras), "output": {"extra": len(extras)}},
            message=f"{len(extras)} extra key(s); sample={_sample_one(extras)[0] if extras else None}",
        ),
        _ok(
            id=f"{check.id}.signature",
            level="hash",
            title="Row signatures",
            entity=entity.name,
            concern=Concern.transformation,
            status=CheckStatus.FAIL if hash_misses else CheckStatus.PASS,
            expected=0,
            actual=len(hash_misses),
            evidence={
                "sample": _sample_one(hash_misses) if hash_misses else _sample_one(hash_matches),
                "fields": [m.target for m in hash_fields],
                "output": {"mismatches": len(hash_misses)},
            },
            message=(
                f"{len(hash_misses)} hashed row(s) differ; sample={_sample_one(hash_misses)[0] if hash_misses else None}"
                if hash_misses
                else f"Row signatures match; sample={_sample_one(hash_matches)[0] if hash_matches else None}"
            ),
        ),
    ]


def run_probes(
    check: SuiteCheck,
    entity: Entity,
    source: Connector | None,
    target: Connector | None,
    params: dict[str, Any],
) -> list[CheckResult]:
    if source is None or target is None:
        return [
            _ok(
                id=check.id,
                level="probes",
                title="Failure-mode probes",
                entity=entity.name,
                concern=Concern.transformation,
                status=CheckStatus.SKIP,
                message="probes require both source and target",
            )
        ]
    probes = check.probes or [
        "truncation",
        "precision",
        "leading_zeros",
        "charset",
        "null_empty_literal",
    ]
    src_f, tgt_f = filters_for(check, params)
    source_info = None
    target_info = None
    try:
        source_info = inspect_relations(source, source_relations(entity))
        target_info = inspect_relations(target, target_relations(entity))
    except Exception:
        pass
    return _run_probes_inline(
        entity, source, target, probes, src_f, tgt_f, source_info, target_info, check.id
    )


def _run_probes_inline(
    entity: Entity,
    source: Connector,
    target: Connector,
    probes: list[str],
    src_f: str | None,
    tgt_f: str | None,
    source_info: TableInfo | None,
    target_info: TableInfo | None,
    check_id: str,
) -> list[CheckResult]:
    source_rows = fetch_concat(source, source_relations(entity), src_f)
    target_rows = _index_by_key(
        fetch_concat(target, target_relations(entity), tgt_f),
        entity.business_key,
    )
    pairs = []
    for source_row in source_rows:
        mapped_key = tuple(
            apply_mapping(source_row, entity.mapping_for_target(col))  # type: ignore[arg-type]
            for col in entity.business_key
        )
        target_row = target_rows.get(mapped_key)
        if target_row is not None:
            pairs.append((source_row, target_row, mapped_key))

    results: list[CheckResult] = []

    if "truncation" in probes:
        hits = []
        for mapping in entity.mappings:
            target_len = mapping.max_length
            if target_info and mapping.target in target_info.columns:
                target_len = target_len or target_info.columns[mapping.target].max_length
            if not mapping.source or not target_len:
                continue
            for source_row, target_row, key in pairs:
                source_value = source_row.get(mapping.source)
                actual = target_row.get(mapping.target)
                if isinstance(source_value, str) and len(source_value) > target_len:
                    hits.append(
                        {
                            "key": dict(zip(entity.business_key, key, strict=True)),
                            "field": mapping.target,
                            "source_length": len(source_value),
                            "target_max": target_len,
                            "target_value": actual,
                        }
                    )
                elif (
                    isinstance(source_value, str)
                    and isinstance(actual, str)
                    and len(actual) < len(source_value.strip())
                ):
                    hits.append(
                        {
                            "key": dict(zip(entity.business_key, key, strict=True)),
                            "field": mapping.target,
                            "source": source_value,
                            "target": actual,
                        }
                    )
        results.append(
            _ok(
                id=f"{check_id}.truncation",
                level="probes",
                title="String truncation",
                entity=entity.name,
                concern=Concern.transformation,
                status=CheckStatus.FAIL if hits else CheckStatus.PASS,
                actual=len(hits),
                evidence={"sample": _sample_one(hits) if hits else []},
                message="Silent truncation detected" if hits else "No truncated strings found",
            )
        )

    if "precision" in probes:
        hits = []
        for mapping in entity.mappings:
            if mapping.normalize.decimal_places is None or not mapping.source:
                continue
            for source_row, target_row, key in pairs:
                source_value = source_row.get(mapping.source)
                actual = target_row.get(mapping.target)
                if source_value is None or actual is None:
                    continue
                if Decimal(str(source_value)) != Decimal(str(actual)):
                    hits.append(
                        {
                            "key": dict(zip(entity.business_key, key, strict=True)),
                            "field": mapping.target,
                            "source": str(source_value),
                            "target": str(actual),
                        }
                    )
        results.append(
            _ok(
                id=f"{check_id}.precision",
                level="probes",
                title="Numeric precision",
                entity=entity.name,
                concern=Concern.transformation,
                status=CheckStatus.FAIL if hits else CheckStatus.PASS,
                actual=len(hits),
                evidence={"sample": _sample_one(hits) if hits else []},
                message="Precision changed in transit" if hits else "Numeric precision preserved",
            )
        )

    if "leading_zeros" in probes:
        hits = []
        for mapping in entity.mappings:
            if not mapping.source:
                continue
            for source_row, target_row, key in pairs:
                source_value = source_row.get(mapping.source)
                actual = target_row.get(mapping.target)
                if (
                    isinstance(source_value, str)
                    and source_value.startswith("0")
                    and str(actual) != source_value
                    and str(actual).lstrip("0") == source_value.lstrip("0")
                ):
                    hits.append(
                        {
                            "key": dict(zip(entity.business_key, key, strict=True)),
                            "field": mapping.target,
                            "source": source_value,
                            "target": actual,
                        }
                    )
        results.append(
            _ok(
                id=f"{check_id}.leading_zeros",
                level="probes",
                title="Leading zeros",
                entity=entity.name,
                concern=Concern.transformation,
                status=CheckStatus.FAIL if hits else CheckStatus.PASS,
                actual=len(hits),
                evidence={"sample": _sample_one(hits) if hits else []},
                message="Leading zeroes were stripped" if hits else "Leading zeroes preserved",
            )
        )

    if "charset" in probes:
        hits = []
        for mapping in entity.mappings:
            if not mapping.source:
                continue
            for source_row, target_row, key in pairs:
                source_value = source_row.get(mapping.source)
                actual = target_row.get(mapping.target)
                if isinstance(source_value, str) and any(ord(ch) > 127 for ch in source_value):
                    if actual != source_value:
                        hits.append(
                            {
                                "key": dict(zip(entity.business_key, key, strict=True)),
                                "field": mapping.target,
                                "source": source_value,
                                "target": actual,
                            }
                        )
        results.append(
            _ok(
                id=f"{check_id}.charset",
                level="probes",
                title="Charset / unicode",
                entity=entity.name,
                concern=Concern.transformation,
                status=CheckStatus.FAIL if hits else CheckStatus.PASS,
                actual=len(hits),
                evidence={"sample": _sample_one(hits) if hits else []},
                message="Non-ASCII values were corrupted" if hits else "Unicode values preserved",
            )
        )

    if "null_empty_literal" in probes:
        hits = []
        for mapping in entity.mappings:
            if not mapping.source:
                continue
            for source_row, target_row, key in pairs:
                source_value = source_row.get(mapping.source)
                actual = target_row.get(mapping.target)
                token = mapping.normalize.null_token
                if source_value in (None, "") or source_value == token:
                    expected_null = None if mapping.normalize.empty_as_null else source_value
                    if canonicalize(expected_null, mapping.normalize) != canonicalize(
                        actual, mapping.normalize
                    ):
                        hits.append(
                            {
                                "key": dict(zip(entity.business_key, key, strict=True)),
                                "field": mapping.target,
                                "source": source_value,
                                "target": actual,
                            }
                        )
        results.append(
            _ok(
                id=f"{check_id}.null_empty_literal",
                level="probes",
                title="NULL / empty literal",
                entity=entity.name,
                concern=Concern.transformation,
                status=CheckStatus.FAIL if hits else CheckStatus.PASS,
                actual=len(hits),
                evidence={"sample": _sample_one(hits) if hits else []},
                message="NULL/empty/literal conversion mismatch" if hits else "Empty-value policy matches",
            )
        )

    if not results:
        results.append(
            _ok(
                id=check_id,
                level="probes",
                title="Failure-mode probes",
                entity=entity.name,
                concern=Concern.transformation,
                status=CheckStatus.SKIP,
                message="No recognized probes selected",
            )
        )
    return results


def run_invariant(
    check: SuiteCheck,
    entity: Entity,
    source: Connector | None,
    target: Connector | None,
    params: dict[str, Any],
) -> list[CheckResult]:
    del source, params
    if target is None:
        return [
            _ok(
                id=check.id,
                level="invariant",
                title="Invariant",
                entity=entity.name,
                concern=Concern.reconciliation,
                status=CheckStatus.SKIP,
                message="invariant requires target connection",
            )
        ]
    spec = check.invariant
    if spec is None:
        raise ValueError(f"Check {check.id}: invariant kind requires invariant: block")
    return _eval_invariant(check.id, entity, target, spec)


def _eval_invariant(
    check_id: str, entity: Entity, target: Connector, invariant: InvariantSpec
) -> list[CheckResult]:
    tgt_refs = target_relations(entity)
    multi_target = len(tgt_refs) > 1
    evidence: dict[str, Any] = {}
    name = invariant.name or check_id

    if invariant.kind == "temporal":
        if multi_target or _is_api_dialect(target):
            rows = fetch_concat(target, tgt_refs, None)
            bad = 0
            for row in rows:
                for left, right in zip(invariant.columns, invariant.columns[1:], strict=False):
                    left_val, right_val = row.get(left), row.get(right)
                    if left_val is not None and right_val is not None and right_val < left_val:
                        bad += 1
                        break
            numeric = bad
            evidence = {"mode": "in_process", "columns": invariant.columns}
        else:
            from migrate_prove.connectors import where_clause

            comparisons = []
            for left, right in zip(invariant.columns, invariant.columns[1:], strict=False):
                lq, rq = target.quote(left), target.quote(right)
                comparisons.append(f"({rq} IS NOT NULL AND {lq} IS NOT NULL AND {rq} < {lq})")
            predicate = " OR ".join(comparisons) if comparisons else "0=1"
            sql = f"SELECT COUNT(*) FROM {target.relation_sql(tgt_refs[0])} WHERE ({predicate})"
            numeric = target.scalar(sql)
            evidence = {"sql": sql}
    elif invariant.kind == "orphans":
        if multi_target or _is_api_dialect(target):
            return [
                _ok(
                    id=check_id,
                    level="invariant",
                    title=f"Invariant {name}",
                    entity=entity.name,
                    concern=invariant.concern,
                    status=CheckStatus.SKIP,
                    message="Orphan invariants require a single SQL target relation",
                )
            ]
        child = target.qualified(invariant.child_table or "", None)
        parent = target.qualified(invariant.parent_table or "", None)
        sql = (
            f"SELECT COUNT(*) FROM {child} child "
            f"LEFT JOIN {parent} parent ON parent.{target.quote(invariant.parent_key or '')} "
            f"= child.{target.quote(invariant.child_key or '')} "
            f"WHERE parent.{target.quote(invariant.parent_key or '')} IS NULL"
        )
        numeric = target.scalar(sql)
        evidence = {"sql": sql}
    else:
        if multi_target or _is_api_dialect(target):
            return [
                _ok(
                    id=check_id,
                    level="invariant",
                    title=f"Invariant {name}",
                    entity=entity.name,
                    concern=invariant.concern,
                    status=CheckStatus.SKIP,
                    message="Raw SQL invariants require a single SQL target relation",
                )
            ]
        sql = invariant.sql or "SELECT 0"
        numeric = target.scalar(sql)
        evidence = {"sql": sql}

    numeric = 0 if numeric is None else numeric
    status = CheckStatus.PASS if numeric == invariant.expect else CheckStatus.FAIL
    evidence = {
        **evidence,
        "output": {"actual": numeric, "expect": invariant.expect},
        "sample": [{"actual": numeric, "expect": invariant.expect}],
    }
    return [
        _ok(
            id=check_id,
            level="invariant",
            title=f"Invariant {name}",
            entity=entity.name,
            concern=invariant.concern,
            status=status,
            expected=invariant.expect,
            actual=numeric,
            variance=None if not isinstance(numeric, (int, float)) else numeric - invariant.expect,
            message=(
                f"actual={numeric}, expect={invariant.expect}"
                + (
                    " — target remains internally consistent"
                    if status == CheckStatus.PASS
                    else " — business invariant broken"
                )
            ),
            evidence=evidence,
        )
    ]


_RUNNERS = {
    "schema": lambda c, e, s, t, p: run_schema_check(
        e, s, t, check_id=c.id, hard=True
    ),
    "count": run_count,
    "group_count": run_group_count,
    "sum": run_sum,
    "group_sum": run_group_sum,
    "transforms": run_transforms,
    "row_sample": run_row_sample,
    "hash": run_hash,
    "probes": run_probes,
    "invariant": run_invariant,
}


def run_check(
    check: SuiteCheck,
    entity: Entity,
    source: Connector | None,
    target: Connector | None,
    params: dict[str, Any],
) -> list[CheckResult]:
    runner = _RUNNERS[check.kind]
    if check.kind == "schema":
        return runner(check, entity, source, target, params)
    return runner(check, entity, source, target, params)
