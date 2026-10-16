from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Any

from migrate_prove.connectors import Connector, RelationRef, TableInfo
from migrate_prove.hashing import row_signature
from migrate_prove.models import (
    CheckResult,
    CheckStatus,
    Concern,
    Entity,
    MappingKind,
)
from migrate_prove.normalize import canonicalize
from migrate_prove.rules import apply_mapping
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
    """Coerce query/row values into JSON-friendly evidence."""
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
    """Keep a single evidence row for multi-row results (prefer mismatches when given)."""
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


def check_schema(entity: Entity, source: Connector, target: Connector) -> list[CheckResult]:
    results: list[CheckResult] = []
    src_refs = source_relations(entity)
    tgt_refs = target_relations(entity)
    try:
        source_info = inspect_relations(source, src_refs)
        target_info = inspect_relations(target, tgt_refs)
    except Exception as exc:
        return [
            _ok(
                id=f"L1.{entity.name}.schema",
                level="1",
                title="Schema objects exist",
                entity=entity.name,
                concern=Concern.migration,
                status=CheckStatus.ERROR,
                message=str(exc),
            )
        ]

    required_source = [mapping.source for mapping in entity.mappings if mapping.source]
    required_target = [mapping.target for mapping in entity.mappings]
    source_part_gaps = missing_columns_on_parts(source, src_refs, required_source)
    target_part_gaps = missing_columns_on_parts(target, tgt_refs, required_target)
    missing_source = sorted(
        {col for cols in source_part_gaps.values() for col in cols}
        | {
            mapping.source
            for mapping in entity.mappings
            if mapping.source and mapping.source not in source_info.columns
        }
    )
    missing_target = sorted(
        {col for cols in target_part_gaps.values() for col in cols}
        | {
            mapping.target
            for mapping in entity.mappings
            if mapping.target not in target_info.columns
        }
    )
    results.append(
        _ok(
            id=f"L1.{entity.name}.columns",
            level="1",
            title="Mapped columns exist",
            entity=entity.name,
            concern=Concern.migration,
            status=CheckStatus.FAIL if missing_source or missing_target else CheckStatus.PASS,
            expected={"source_missing": [], "target_missing": []},
            actual={
                "source_missing": missing_source,
                "target_missing": missing_target,
                "source_part_gaps": source_part_gaps,
                "target_part_gaps": target_part_gaps,
            },
            message=(
                "Missing columns on source or target"
                if missing_source or missing_target
                else "All mapped columns are present"
            ),
        )
    )

    nullability_mismatches = []
    for mapping in entity.mappings:
        column = target_info.columns.get(mapping.target)
        if column and not mapping.nullable and column.nullable:
            nullability_mismatches.append(mapping.target)
    results.append(
        _ok(
            id=f"L1.{entity.name}.nullability",
            level="1",
            title="Target nullability vs contract",
            entity=entity.name,
            concern=Concern.migration,
            status=CheckStatus.FAIL if nullability_mismatches else CheckStatus.PASS,
            actual=nullability_mismatches,
            message=(
                "Contract required NOT NULL but target allows NULL"
                if nullability_mismatches
                else "Nullability matches the contract"
            ),
        )
    )
    return results


def check_volume(entity: Entity, source: Connector, target: Connector) -> list[CheckResult]:
    src = source_relations(entity)
    tgt = target_relations(entity)
    source_count = count_concat(source, src, entity.filter_source)
    target_count = count_concat(target, tgt, entity.filter_target)
    matched = source_count == target_count
    results = [
        _ok(
            id=f"L2.{entity.name}.volume.total",
            level="2",
            title="Total row count",
            entity=entity.name,
            concern=Concern.migration,
            status=CheckStatus.PASS if matched else CheckStatus.FAIL,
            expected=source_count,
            actual=target_count,
            variance=target_count - source_count,
            evidence={
                "output": {"source": source_count, "target": target_count},
                "sample": [
                    {
                        "source": source_count,
                        "target": target_count,
                        "variance": target_count - source_count,
                    }
                ],
            },
            message=(
                f"COUNT(*) source={source_count}, target={target_count}"
                + (
                    " — totals match (can still mask sliced defects)"
                    if matched
                    else " — totals differ"
                )
            ),
        )
    ]

    for dimensions in entity.volume_slices:
        source_dim_sql = {
            dim: mapped_dimension_sql(source, entity, dim) for dim in dimensions
        }
        # API targets cannot evaluate SQL CASE lookups; group on projected field names.
        source_groups = group_count_concat(
            source, src, dimensions, entity.filter_source, source_dim_sql
        )
        target_groups = group_count_concat(target, tgt, dimensions, entity.filter_target)
        source_map = {
            tuple(row[dim] for dim in dimensions): int(row["row_count"]) for row in source_groups
        }
        target_map = {
            tuple(row[dim] for dim in dimensions): int(row["row_count"]) for row in target_groups
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
                    "slice": dict(zip(dimensions, key, strict=True)),
                    "source": expected,
                    "target": actual,
                    "variance": variance,
                }
            )
        label = ",".join(dimensions)
        sample = _sample_one(slices, prefer=lambda row: row.get("variance") not in (0, 0.0, None))
        results.append(
            _ok(
                id=f"L2.{entity.name}.volume.by_{label}",
                level="2",
                title=f"Volume by {label}",
                entity=entity.name,
                concern=Concern.transformation if failed else Concern.migration,
                status=CheckStatus.FAIL if failed else CheckStatus.PASS,
                expected={_slice_label(key): source_map[key] for key in source_map},
                actual={_slice_label(key): target_map[key] for key in target_map},
                evidence={
                    "output": {"source_groups": len(source_map), "target_groups": len(target_map)},
                    "slices": slices,
                    "sample": sample,
                },
                message=(
                    f"GROUP BY {label}: {len(slices)} slice(s); sample={sample[0] if sample else None}"
                    + (
                        " — segmented counts differ"
                        if failed
                        else " — segmented counts match"
                    )
                ),
            )
        )
    return results


def check_metrics(entity: Entity, source: Connector, target: Connector) -> list[CheckResult]:
    results: list[CheckResult] = []
    src = source_relations(entity)
    tgt = target_relations(entity)
    for metric in entity.metrics:
        if metric.group_by:
            source_dim_sql = {
                dim: mapped_dimension_sql(source, entity, dim) for dim in metric.group_by
            }
            source_rows = {
                tuple(row[dim] for dim in metric.group_by): float(row["metric"] or 0)
                for row in aggregate_grouped_concat(
                    source,
                    src,
                    metric.source_sql,
                    metric.group_by,
                    entity.filter_source,
                    source_dim_sql,
                )
            }
            target_dim_sql = None if _is_api_dialect(target) else None
            target_rows = {
                tuple(row[dim] for dim in metric.group_by): float(row["metric"] or 0)
                for row in aggregate_grouped_concat(
                    target,
                    tgt,
                    metric.target_sql,
                    metric.group_by,
                    entity.filter_target,
                    target_dim_sql,
                )
            }
            keys = sorted(set(source_rows) | set(target_rows), key=str)
            failed = False
            slices = []
            for key in keys:
                expected = source_rows.get(key, 0.0)
                actual = target_rows.get(key, 0.0)
                ok = _within_tolerance(expected, actual, metric.tolerance_abs, metric.tolerance_pct)
                if not ok:
                    failed = True
                slices.append(
                    {
                        "slice": dict(zip(metric.group_by, key, strict=True)),
                        "source": expected,
                        "target": actual,
                        "variance": actual - expected,
                    }
                )
            sample = _sample_one(
                slices,
                prefer=lambda row: not _within_tolerance(
                    float(row["source"]),
                    float(row["target"]),
                    metric.tolerance_abs,
                    metric.tolerance_pct,
                ),
            )
            results.append(
                _ok(
                    id=f"L3.{entity.name}.metric.{metric.name}",
                    level="3",
                    title=f"Metric {metric.name}",
                    entity=entity.name,
                    concern=Concern.reconciliation,
                    status=CheckStatus.FAIL if failed else CheckStatus.PASS,
                    expected={_slice_label(key): source_rows[key] for key in source_rows},
                    actual={_slice_label(key): target_rows[key] for key in target_rows},
                    evidence={
                        "output": {
                            "source_sql": metric.source_sql,
                            "target_sql": metric.target_sql,
                            "group_by": metric.group_by,
                        },
                        "slices": slices,
                        "sample": sample,
                    },
                    message=(
                        f"{metric.source_sql} vs {metric.target_sql}; "
                        f"{len(slices)} group(s); sample={sample[0] if sample else None}"
                        + (" — mismatch" if failed else " — within tolerance")
                    ),
                )
            )
            continue

        source_value = aggregate_concat(source, src, metric.source_sql, entity.filter_source)
        target_value = aggregate_concat(target, tgt, metric.target_sql, entity.filter_target)
        expected = float(source_value or 0)
        actual = float(target_value or 0)
        ok = _within_tolerance(expected, actual, metric.tolerance_abs, metric.tolerance_pct)
        results.append(
            _ok(
                id=f"L3.{entity.name}.metric.{metric.name}",
                level="3",
                title=f"Metric {metric.name}",
                entity=entity.name,
                concern=Concern.reconciliation,
                status=CheckStatus.PASS if ok else CheckStatus.FAIL,
                expected=expected,
                actual=actual,
                variance=actual - expected,
                evidence={
                    "output": {
                        "source": expected,
                        "target": actual,
                        "source_sql": metric.source_sql,
                        "target_sql": metric.target_sql,
                    },
                    "sample": [{"source": expected, "target": actual, "variance": actual - expected}],
                },
                message=(
                    f"{metric.source_sql}={expected}, {metric.target_sql}={actual}"
                    + (" — within tolerance" if ok else " — outside tolerance")
                ),
            )
        )
    return results


def _expected_row(source_row: dict[str, Any], entity: Entity) -> dict[str, Any]:
    expected: dict[str, Any] = {}
    for mapping in entity.mappings:
        if mapping.kind == MappingKind.surrogate:
            continue
        expected[mapping.target] = apply_mapping(source_row, mapping)
    return expected


def check_transforms(entity: Entity, source: Connector, target: Connector) -> list[CheckResult]:
    source_rows = fetch_concat(source, source_relations(entity), entity.filter_source)
    target_rows = _index_by_key(
        fetch_concat(target, target_relations(entity), entity.filter_target),
        entity.business_key,
    )
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
        for mapping in entity.mappings:
            if mapping.kind == MappingKind.surrogate:
                continue
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
    transform_fields = [
        mapping
        for mapping in entity.mappings
        if mapping.kind in {MappingKind.transform, MappingKind.coerce} or mapping.lookup or mapping.rules
    ]
    if not transform_fields:
        transform_fields = [m for m in entity.mappings if m.kind != MappingKind.surrogate]
    for mapping in transform_fields:
        misses = mismatches.get(mapping.target, [])
        sample = (_sample_one(misses) if misses else _sample_one(matches.get(mapping.target, [])))
        results.append(
            _ok(
                id=f"L4.{entity.name}.transform.{mapping.target}",
                level="4",
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
    return results


def check_row_hashes(entity: Entity, source: Connector, target: Connector) -> list[CheckResult]:
    hash_fields = [m for m in entity.mappings if m.in_hash and m.kind != MappingKind.surrogate]
    source_rows = fetch_concat(source, source_relations(entity), entity.filter_source)
    target_rows = _index_by_key(
        fetch_concat(target, target_relations(entity), entity.filter_target),
        entity.business_key,
    )
    source_keys = set()
    missing: list[Any] = []
    extras: list[Any] = []
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
    extras = [dict(zip(entity.business_key, key, strict=True)) for key in sorted(target_keys - source_keys, key=str)]
    present_sample = _sample_one(
        [
            dict(zip(entity.business_key, key, strict=True))
            for key in sorted(source_keys & target_keys, key=str)[:1]
        ]
    )

    return [
        _ok(
            id=f"HASH.{entity.name}.keys.missing",
            level="hash",
            title="Keys present in source, missing in target",
            entity=entity.name,
            concern=Concern.migration,
            status=CheckStatus.FAIL if missing else CheckStatus.PASS,
            expected=0,
            actual=len(missing),
            evidence={
                "sample": _sample_one(missing) if missing else present_sample,
                "output": {"missing": len(missing), "source_keys": len(source_keys)},
            },
            message=(
                f"{len(missing)} missing key(s); sample={_sample_one(missing)[0] if missing else None}"
                if missing
                else f"0 missing of {len(source_keys)} source key(s); sample={present_sample[0] if present_sample else None}"
            ),
        ),
        _ok(
            id=f"HASH.{entity.name}.keys.extra",
            level="hash",
            title="Keys present in target, missing in source",
            entity=entity.name,
            concern=Concern.migration,
            status=CheckStatus.FAIL if extras else CheckStatus.PASS,
            expected=0,
            actual=len(extras),
            evidence={
                "sample": _sample_one(extras) if extras else present_sample,
                "output": {"extra": len(extras), "target_keys": len(target_keys)},
            },
            message=(
                f"{len(extras)} extra key(s); sample={_sample_one(extras)[0] if extras else None}"
                if extras
                else f"0 extras of {len(target_keys)} target key(s); sample={present_sample[0] if present_sample else None}"
            ),
        ),
        _ok(
            id=f"HASH.{entity.name}.row_signature",
            level="hash",
            title="Deterministic row signatures",
            entity=entity.name,
            concern=Concern.transformation,
            status=CheckStatus.FAIL if hash_misses else CheckStatus.PASS,
            expected=0,
            actual=len(hash_misses),
            evidence={
                "sample": _sample_one(hash_misses) if hash_misses else _sample_one(hash_matches),
                "fields": [m.target for m in hash_fields],
                "output": {"mismatches": len(hash_misses), "matched": len(source_keys) - len(missing) - len(hash_misses)},
            },
            message=(
                f"{len(hash_misses)} hashed row(s) differ; sample={_sample_one(hash_misses)[0] if hash_misses else None}"
                if hash_misses
                else (
                    f"Row signatures match; sample={_sample_one(hash_matches)[0] if hash_matches else None}"
                )
            ),
        ),
    ]


def _temporal_count_sql(connector: Connector, ref: RelationRef, columns: list[str], extra_where: str | None) -> str:
    from migrate_prove.connectors import where_clause

    comparisons = []
    for left, right in zip(columns, columns[1:], strict=False):
        lq, rq = connector.quote(left), connector.quote(right)
        comparisons.append(f"({rq} IS NOT NULL AND {lq} IS NOT NULL AND {rq} < {lq})")
    predicate = " OR ".join(comparisons) if comparisons else "0=1"
    base_where = where_clause(extra_where)
    joiner = " AND " if extra_where else " WHERE "
    return (
        f"SELECT COUNT(*) FROM {connector.relation_sql(ref)}"
        f"{base_where}{joiner}({predicate})"
    )


def check_invariants(entity: Entity, target: Connector) -> list[CheckResult]:
    results: list[CheckResult] = []
    tgt_refs = target_relations(entity)
    multi_target = len(tgt_refs) > 1
    for invariant in entity.invariants:
        evidence: dict[str, Any] = {}
        if invariant.kind == "temporal":
            if multi_target or _is_api_dialect(target):
                rows = fetch_concat(target, tgt_refs, entity.filter_target)
                bad = 0
                for row in rows:
                    for left, right in zip(invariant.columns, invariant.columns[1:], strict=False):
                        left_val, right_val = row.get(left), row.get(right)
                        if left_val is not None and right_val is not None and right_val < left_val:
                            bad += 1
                            break
                numeric = bad
                evidence = {"mode": "in_process", "columns": invariant.columns, "parts": len(tgt_refs)}
            else:
                tgt = tgt_refs[0]
                sql = _temporal_count_sql(target, tgt, invariant.columns, entity.filter_target)
                numeric = target.scalar(sql)
                evidence = {"sql": sql}
        elif invariant.kind == "orphans":
            if multi_target or _is_api_dialect(target):
                results.append(
                    _ok(
                        id=f"L5.{entity.name}.invariant.{invariant.name}",
                        level="5",
                        title=f"Invariant {invariant.name}",
                        entity=entity.name,
                        concern=invariant.concern,
                        status=CheckStatus.SKIP,
                        message=(
                            "Orphan invariants require a single SQL target relation; "
                            "skipped for multi-part or API targets"
                        ),
                    )
                )
                continue
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
                results.append(
                    _ok(
                        id=f"L5.{entity.name}.invariant.{invariant.name}",
                        level="5",
                        title=f"Invariant {invariant.name}",
                        entity=entity.name,
                        concern=invariant.concern,
                        status=CheckStatus.SKIP,
                        message=(
                            "Raw SQL invariants require a single SQL target relation; "
                            "skipped for multi-part or API targets"
                        ),
                    )
                )
                continue
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
        results.append(
            _ok(
                id=f"L5.{entity.name}.invariant.{invariant.name}",
                level="5",
                title=f"Invariant {invariant.name}",
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
        )
    return results


def check_failure_probes(
    entity: Entity,
    source: Connector,
    target: Connector,
    source_info: TableInfo | None,
    target_info: TableInfo | None,
) -> list[CheckResult]:
    probes = entity.failure_probes
    if not probes:
        return []
    source_rows = fetch_concat(source, source_relations(entity), entity.filter_source)
    target_rows = _index_by_key(
        fetch_concat(target, target_relations(entity), entity.filter_target),
        entity.business_key,
    )
    results: list[CheckResult] = []

    def paired() -> list[tuple[dict[str, Any], dict[str, Any], tuple]]:
        pairs = []
        for source_row in source_rows:
            mapped_key = tuple(
                apply_mapping(source_row, entity.mapping_for_target(col))  # type: ignore[arg-type]
                for col in entity.business_key
            )
            target_row = target_rows.get(mapped_key)
            if target_row is not None:
                pairs.append((source_row, target_row, mapped_key))
        return pairs

    pairs = paired()

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
                            "target_length": len(actual) if isinstance(actual, str) else None,
                            "target_value": actual,
                        }
                    )
                elif isinstance(source_value, str) and isinstance(actual, str) and len(actual) < len(source_value.strip()):
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
                id=f"PROBE.{entity.name}.truncation",
                level="probes",
                title="String truncation",
                entity=entity.name,
                concern=Concern.transformation,
                status=CheckStatus.FAIL if hits else CheckStatus.PASS,
                actual=len(hits),
                evidence={"sample": hits[:20]},
                message="Silent truncation detected" if hits else "No truncated strings found",
            )
        )

    if "precision" in probes:
        hits = []
        for mapping in entity.mappings:
            if mapping.normalize.decimal_places is None:
                continue
            for source_row, target_row, key in pairs:
                if not mapping.source:
                    continue
                source_value = source_row.get(mapping.source)
                actual = target_row.get(mapping.target)
                if source_value is None or actual is None:
                    continue
                source_dec = Decimal(str(source_value))
                target_dec = Decimal(str(actual))
                if source_dec != target_dec:
                    hits.append(
                        {
                            "key": dict(zip(entity.business_key, key, strict=True)),
                            "field": mapping.target,
                            "source": str(source_dec),
                            "target": str(target_dec),
                        }
                    )
        results.append(
            _ok(
                id=f"PROBE.{entity.name}.precision",
                level="probes",
                title="Decimal precision loss",
                entity=entity.name,
                concern=Concern.transformation,
                status=CheckStatus.FAIL if hits else CheckStatus.PASS,
                actual=len(hits),
                evidence={"sample": hits[:20]},
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
                if isinstance(source_value, str) and source_value.startswith("0") and source_value != str(actual):
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
                id=f"PROBE.{entity.name}.leading_zeros",
                level="probes",
                title="Leading-zero identifiers",
                entity=entity.name,
                concern=Concern.transformation,
                status=CheckStatus.FAIL if hits else CheckStatus.PASS,
                actual=len(hits),
                evidence={"sample": hits[:20]},
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
                id=f"PROBE.{entity.name}.charset",
                level="probes",
                title="Unicode / encoding fidelity",
                entity=entity.name,
                concern=Concern.transformation,
                status=CheckStatus.FAIL if hits else CheckStatus.PASS,
                actual=len(hits),
                evidence={"sample": hits[:20]},
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
                source_is_empty = source_value in ("", "NULL", "N/A") or source_value is None
                if source_is_empty and canonicalize(source_value, mapping.normalize) != canonicalize(
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
                id=f"PROBE.{entity.name}.null_empty_literal",
                level="probes",
                title="NULL vs empty vs literal handling",
                entity=entity.name,
                concern=Concern.transformation,
                status=CheckStatus.FAIL if hits else CheckStatus.PASS,
                actual=len(hits),
                evidence={"sample": hits[:20]},
                message="NULL/empty/literal conversion mismatch" if hits else "Empty-value policy matches the contract",
            )
        )

    return results
