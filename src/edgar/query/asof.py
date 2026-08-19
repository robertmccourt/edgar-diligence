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

    A figure is identified by (canonical_field, period_start, period_end,
    period_type, unit) — never by period_end alone. Every 10-Q reports a
    three-month and a year-to-date figure that end on the same day, and a
    10-K adds annual and comparative figures; those are distinct economic
    quantities of different lengths, not versions of one another. For each
    such figure the row with the greatest filed_date not later than as_of
    wins. Rows filed after as_of are invisible — this is the point-in-time
    guarantee.

    `unit` is part of that identity. Filers report the same balance-sheet
    line in more than one currency: 1,300 figure keys in the 2024 Q1-Q2
    smoke build span more than one unit, and cik 1296774 filed total_assets
    as both USD 205,617,546 and CNY 1,310,318,375 for the same date in the
    same accession. Without unit in the partition those two compete for
    rank 1 and the query returns whichever the tiebreak happens to pick —
    a 6.4x error handed to the caller under a valid fact_id. The same
    figure in two currencies is two figures; both stay independently
    queryable. No conversion or currency preference is applied here: that
    is a decision for the consumer, who can see both.

    `period_start` and `period_end` bound the fact's `period_end` column
    only — `period_start` is never compared against the `period_start`
    column. A duration fact that began before `period_start` is still
    returned as long as it ends within [period_start, period_end]. This is
    intentional: an as-of query selects facts describing a period that
    *ends* inside the requested window, not facts fully contained by it.
    """
    if not fields:
        return []
    placeholders = ", ".join("?" for _ in fields)
    rows = con.execute(
        f"""
        SELECT {_COLUMNS} FROM (
            SELECT {_COLUMNS},
                   row_number() OVER (
                       PARTITION BY canonical_field, period_start,
                                    period_end, period_type, unit
                       ORDER BY filed_date DESC, accession DESC,
                                fact_id DESC
                   ) AS rn
            FROM fact
            WHERE cik = ?
              AND canonical_field IN ({placeholders})
              AND period_end BETWEEN ? AND ?
              AND filed_date <= ?
        ) WHERE rn = 1
        ORDER BY canonical_field, period_end, period_start, unit
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
