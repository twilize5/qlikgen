"""
CSV profiling with DuckDB.

Local, deterministic, no Qlik and no LLM. Reads the uploaded CSV, infers a Qlik
logical type per column, classifies each column as dimension / measure / temporal,
and gathers light stats for display. The resulting CsvProfile is the single source
of truth that script_gen and template_builder both read — so the LOAD script and
the chart field references can never drift apart.
"""
from __future__ import annotations

import re
from pathlib import Path

import duckdb

from .models import ColumnProfile, CsvProfile, ColumnRole, QlikType

# Columns whose distinct count is at least this fraction of the row count and
# which are integer-typed are treated as keys (dimensions), not measures — so we
# don't end up summing an auto-increment ID column in a KPI. Only applied once
# the table is big enough that near-uniqueness is meaningful; on a tiny sample
# every integer column looks unique.
_KEY_UNIQUE_RATIO = 0.95
_KEY_MIN_ROWS = 50


def _map_type(duck_type: str) -> QlikType:
    t = duck_type.upper()
    if t.startswith("DATE"):
        return "date"
    if t.startswith("TIMESTAMP"):
        return "timestamp"
    if t.startswith(("BIGINT", "INTEGER", "SMALLINT", "TINYINT", "HUGEINT",
                     "UBIGINT", "UINTEGER", "USMALLINT", "UTINYINT")):
        return "integer"
    if t.startswith(("DOUBLE", "FLOAT", "REAL", "DECIMAL", "NUMERIC")):
        return "double"
    # BOOLEAN, TIME, VARCHAR, BLOB, etc. -> treat as a string dimension.
    return "string"


def _looks_like_key(name: str, qtype: QlikType, distinct: int, rows: int) -> bool:
    low = name.strip().lower()
    # Name-based: an explicit id column, regardless of table size or type.
    if low == "id" or low.endswith("_id") or low.endswith(" id"):
        return True
    # Shape-based: a near-unique integer column in a non-trivial table (a PK).
    if (
        qtype == "integer"
        and rows >= _KEY_MIN_ROWS
        and distinct >= _KEY_UNIQUE_RATIO * rows
    ):
        return True
    return False


def _role(name: str, qtype: QlikType, distinct: int, rows: int) -> ColumnRole:
    if qtype in ("date", "timestamp"):
        return "temporal"
    if qtype in ("integer", "double"):
        if _looks_like_key(name, qtype, distinct, rows):
            return "dimension"
        return "measure"
    return "dimension"


def _sanitise_table_name(filename: str) -> str:
    stem = Path(filename).stem or "Data"
    # Qlik table names: keep it simple/identifier-ish for the LOAD label.
    cleaned = re.sub(r"[^0-9A-Za-z_]+", "_", stem).strip("_")
    if not cleaned or cleaned[0].isdigit():
        cleaned = f"T_{cleaned}" if cleaned else "Data"
    return cleaned


def profile_csv(path: str | Path, filename: str) -> CsvProfile:
    """Profile a CSV file on disk. `filename` is the original upload name."""
    p = str(path)
    con = duckdb.connect()
    try:
        # SUMMARIZE gives us name/type/min/max/approx_unique/null_percentage in one pass.
        summary = con.execute(
            "SUMMARIZE SELECT * FROM read_csv_auto(?, SAMPLE_SIZE=-1)", [p]
        ).fetchall()
        summary_cols = [d[0] for d in con.description]

        row_count = con.execute(
            "SELECT count(*) FROM read_csv_auto(?, SAMPLE_SIZE=-1)", [p]
        ).fetchone()[0]

        # A few sample rows for display (best-effort).
        sample_rows = con.execute(
            "SELECT * FROM read_csv_auto(?, SAMPLE_SIZE=-1) LIMIT 5", [p]
        ).fetchall()
        sample_field_names = [d[0] for d in con.description]
    finally:
        con.close()

    idx = {name: i for i, name in enumerate(summary_cols)}

    def _val(row, key):
        i = idx.get(key)
        return row[i] if i is not None else None

    columns: list[ColumnProfile] = []
    for row in summary:
        name = _val(row, "column_name")
        duck_type = _val(row, "column_type") or "VARCHAR"
        qtype = _map_type(str(duck_type))

        distinct_raw = _val(row, "approx_unique")
        distinct = int(distinct_raw) if distinct_raw is not None else 0

        null_raw = _val(row, "null_percentage")
        try:
            null_pct = round(float(null_raw), 2) if null_raw is not None else 0.0
        except (TypeError, ValueError):
            null_pct = 0.0

        role = _role(name, qtype, distinct, int(row_count))

        # Column samples pulled from the LIMIT 5 rows.
        samples: list[str] = []
        if name in sample_field_names:
            si = sample_field_names.index(name)
            for r in sample_rows:
                v = r[si]
                if v is not None:
                    samples.append(str(v))
                if len(samples) >= 3:
                    break

        columns.append(
            ColumnProfile(
                name=str(name),
                qlik_type=qtype,
                role=role,
                distinct=distinct,
                null_pct=null_pct,
                min=None if _val(row, "min") is None else str(_val(row, "min")),
                max=None if _val(row, "max") is None else str(_val(row, "max")),
                samples=samples,
            )
        )

    return CsvProfile(
        table_name=_sanitise_table_name(filename),
        filename=filename,
        row_count=int(row_count),
        columns=columns,
    )
