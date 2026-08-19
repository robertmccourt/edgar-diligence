import duckdb

# A "figure" is one (cik, canonical_field, period_start, period_end,
# period_type, unit) key. It is restated when two filings report materially
# different values for it.
#
# period_start is load-bearing, not decoration. Every 10-Q reports a
# three-month and a year-to-date figure ending on the same day, and a 10-K
# adds annual and comparative figures. Keyed on period_end alone, those
# distinct quantities are mistaken for versions of one another and their
# difference is reported as a restatement — which is what produced a 54.2%
# median absolute change and a 49,999,630% maximum against real filings.
#
# `unit` is load-bearing for the same reason. 1,300 figure keys in the
# 2024 Q1-Q2 smoke build span more than one unit: a filer reporting the
# same line in USD and CNY is not restating it, and scoring the exchange
# rate as a restatement inflates both the rate and the median change.
_VERSIONS = """
WITH ordered AS (
    SELECT cik, canonical_field, period_start, period_end, period_type,
           unit, value, filed_date,
           first_value(value) OVER (
               PARTITION BY cik, canonical_field, period_start, period_end,
                            period_type, unit
               ORDER BY filed_date
           ) AS first_value,
           count(*) OVER (
               PARTITION BY cik, canonical_field, period_start, period_end,
                            period_type, unit
           ) AS n_versions
    FROM fact
),
figures AS (
    SELECT cik, canonical_field, period_start, period_end, period_type,
           unit, any_value(first_value) AS original,
           max(n_versions) AS n_versions,
           max(CASE WHEN first_value <> 0
                    THEN abs(value - first_value) / abs(first_value) * 100
                    ELSE 0 END) AS abs_pct_change
    FROM ordered
    GROUP BY cik, canonical_field, period_start, period_end, period_type, unit
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
               n_versions, abs_pct_change, unit
        FROM figures
        WHERE abs_pct_change >= ? AND abs_pct_change > 0.0001
        ORDER BY abs_pct_change DESC
        """,
        [min_abs_pct],
    ).fetchall()


def filing_lag_stats(con: duckdb.DuckDBPyConnection) -> dict:
    """How long after its period ended did each filing reach the SEC?

    Measured on `raw_sub`, which holds one row per filing carrying both the
    period it covers and the date it was filed. This is deliberately not
    measured on `fact`: a single 10-K also restates the prior year, so a
    report filed 45 days after its own year end contributes a ~410-day lag
    for the comparative period it happens to mention. Measuring per filing
    asks the question the metric is named for.

    Restricted to 10-K and 10-Q — the periodic reports whose deadlines the
    lag is meaningfully compared against. Amendments (10-K/A, 10-Q/A) are
    excluded: they are corrections filed long after the fact and describe
    how late a correction was, not how promptly the company reported.

    `raw_sub.period` is blank for some filers; those rows carry no period to
    measure from and are dropped rather than folded in as a zero.
    """
    row = con.execute(
        """
        WITH lags AS (
            SELECT date_diff('day',
                             try_strptime(trim(period), '%Y%m%d')::DATE,
                             try_strptime(trim(filed), '%Y%m%d')::DATE
                    ) AS lag_days
            FROM raw_sub
            WHERE form IN ('10-K', '10-Q')
        )
        SELECT count(*), median(lag_days), quantile_cont(lag_days, 0.9)
        FROM lags
        WHERE lag_days IS NOT NULL
        """
    ).fetchone()
    return {"n": row[0], "median_days": row[1], "p90_days": row[2]}
