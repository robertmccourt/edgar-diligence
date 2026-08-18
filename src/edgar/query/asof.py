# src/edgar/query/asof.py
from dataclasses import dataclass
from datetime import date
import duckdb

_COLUMNS = """
    fact_id, cik, canonical_field, value, unit, period_type,
    period_start, period_end, filed_date, accession, source_tag,
    mapping_rule_id
"""


@dataclass(frozen=True)
class AsOfFact:
    fact_id: str
    cik: int
    canonical_field: str
    value: float
    unit: str
    period_type: str
    period_start: date | None
    period_end: date
    filed_date: date
    accession: str
    source_tag: str
    mapping_rule_id: str


def get_facts_asof(
    con: duckdb.DuckDBPyConnection,
    cik: int,
    fields: list[str],
    period_start: date,
    period_end: date,
    as_of: date,
) -> list[AsOfFact]:
    """Return the value that was knowable on `as_of`.

    For each (field, period) the row with the greatest filed_date not later
    than as_of wins. Rows filed after as_of are invisible — this is the
    point-in-time guarantee.
    """
    if not fields:
        return []
    placeholders = ", ".join("?" for _ in fields)
    rows = con.execute(
        f"""
        SELECT {_COLUMNS} FROM (
            SELECT {_COLUMNS},
                   row_number() OVER (
                       PARTITION BY canonical_field, period_end, period_type
                       ORDER BY filed_date DESC, accession DESC
                   ) AS rn
            FROM fact
            WHERE cik = ?
              AND canonical_field IN ({placeholders})
              AND period_end BETWEEN ? AND ?
              AND filed_date <= ?
        ) WHERE rn = 1
        ORDER BY canonical_field, period_end
        """,
        [cik, *fields, period_start, period_end, as_of],
    ).fetchall()
    return [AsOfFact(*r) for r in rows]


def restatement_history(
    con: duckdb.DuckDBPyConnection,
    cik: int,
    field: str,
    period_end: date,
) -> list[AsOfFact]:
    """Every reported version of one figure, oldest filing first."""
    rows = con.execute(
        f"""
        SELECT {_COLUMNS} FROM fact
        WHERE cik = ? AND canonical_field = ? AND period_end = ?
        ORDER BY filed_date ASC
        """,
        [cik, field, period_end],
    ).fetchall()
    return [AsOfFact(*r) for r in rows]
