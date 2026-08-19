import duckdb

# A "figure" is one (cik, canonical_field, period_start, period_end,
# period_type) key. It is restated when two filings report materially
# different values for it.
#
# period_start is load-bearing, not decoration. Every 10-Q reports a
# three-month and a year-to-date figure ending on the same day, and a 10-K
# adds annual and comparative figures. Keyed on period_end alone, those
# distinct quantities are mistaken for versions of one another and their
# difference is reported as a restatement — which is what produced a 54.2%
# median absolute change and a 49,999,630% maximum against real filings.
_VERSIONS = """
WITH ordered AS (
    SELECT cik, canonical_field, period_start, period_end, period_type,
           value, filed_date,
           first_value(value) OVER (
               PARTITION BY cik, canonical_field, period_start, period_end,
                            period_type
               ORDER BY filed_date
           ) AS first_value,
           count(*) OVER (
               PARTITION BY cik, canonical_field, period_start, period_end,
                            period_type
           ) AS n_versions
    FROM fact
),
figures AS (
    SELECT cik, canonical_field, period_start, period_end, period_type,
           any_value(first_value) AS original,
           max(n_versions) AS n_versions,
           max(CASE WHEN first_value <> 0
                    THEN abs(value - first_value) / abs(first_value) * 100
                    ELSE 0 END) AS abs_pct_change
    FROM ordered
    GROUP BY cik, canonical_field, period_start, period_end, period_type
)
"""


def restatement_stats(con: duckdb.DuckDBPyConnection) -> dict:
    row = con.execute(
        _VERSIONS + """
        SELECT count(*),
               count(*) FILTER (WHERE abs_pct_change > 0.0001),
               median(abs_pct_change) FILTER (WHERE abs_pct_change > 0.0001),
               max(abs_pct_change)
        FROM figures
        """
    ).fetchone()
    total, restated, med, mx = row
    return {
        "total_figures": total,
        "restated_figures": restated,
        "restatement_rate": (restated / total) if total else 0.0,
        "median_abs_pct_change": med or 0.0,
        "max_abs_pct_change": mx or 0.0,
    }


def restatement_detail(
    con: duckdb.DuckDBPyConnection, min_abs_pct: float = 0.0
) -> list[tuple]:
    return con.execute(
        _VERSIONS + """
        SELECT cik, canonical_field, period_start, period_end, original,
               n_versions, abs_pct_change
        FROM figures
        WHERE abs_pct_change >= ? AND abs_pct_change > 0.0001
        ORDER BY abs_pct_change DESC
        """,
        [min_abs_pct],
    ).fetchall()


def filing_lag_stats(con: duckdb.DuckDBPyConnection) -> dict:
    row = con.execute(
        """
        WITH first_filing AS (
            SELECT cik, canonical_field, period_end,
                   min(filed_date) AS first_filed
            FROM fact
            GROUP BY cik, canonical_field, period_end
        )
        SELECT count(*),
               median(date_diff('day', period_end, first_filed)),
               quantile_cont(date_diff('day', period_end, first_filed), 0.9)
        FROM first_filing
        """
    ).fetchone()
    return {"n": row[0], "median_days": row[1], "p90_days": row[2]}
