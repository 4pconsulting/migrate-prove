from __future__ import annotations

from migrate_prove.connectors import Connector, RelationRef
from migrate_prove.models import SamplePolicy


def _key_sql(connector: Connector, columns: list[str]) -> str:
    quoted = [connector.quote(column) for column in columns]
    if len(quoted) == 1:
        return quoted[0]
    return " || '|' || ".join(quoted)


def select_keys(
    connector: Connector,
    ref: RelationRef,
    key_columns: list[str],
    extra_where: str | None,
    sample: SamplePolicy,
    *,
    for_source: bool = True,
) -> list[tuple]:
    del for_source
    key_select = ", ".join(connector.quote(col) for col in key_columns)
    if sample.strategy == "full" or not sample.strata:
        rows = connector.fetch_rows(ref, extra_where)
        return [tuple(row[col] for col in key_columns) for row in rows]

    # Stratified sampling uses SQL pushdown when the dialect supports it.
    if connector.dialect_name() == "salesforce":
        rows = connector.fetch_rows(ref, extra_where)
        return [tuple(row[col] for col in key_columns) for row in rows]

    from_sql = connector.relation_sql(ref)
    seen: set[tuple] = set()
    ordered: list[tuple] = []
    key_expr = _key_sql(connector, key_columns)
    for stratum in sample.strata:
        clauses = [clause for clause in (extra_where, stratum.where) if clause]
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        if stratum.rate >= 1.0:
            sql = f"SELECT {key_select} FROM {from_sql}{where}"
        else:
            threshold = int(stratum.rate * 10000)
            sql = (
                f"SELECT {key_select} FROM {from_sql}{where} "
                f"{'AND' if where else 'WHERE'} ABS(UNICODE({key_expr}) * 1103515245) % 10000 < {threshold}"
            )
        for row in connector.query(sql):
            key = tuple(row[col] for col in key_columns)
            if key not in seen:
                seen.add(key)
                ordered.append(key)
    return ordered
