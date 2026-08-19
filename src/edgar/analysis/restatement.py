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
#
# "Two filings" is enforced, not assumed. abs_pct_change is measured only
# against rows from a (filed_date, accession) pair other than the one the
# original came from, so two values disagreeing *inside* a single filing
# can never register as a restatement. That kind of disagreement is real
# and worth knowing about — it is reported separately as `ambiguous`, and
# aggregated into the `ambiguous_figures` count, never folded into the
# restatement rate. The two questions have different answers and different
# owners: a restatement is something the filer did later, an ambiguity is
# something the curation layer could not resolve.
#
# The "original" is the first row by (filed_date, accession, value,
# fact_id). Ordering on filed_date alone leaves the original arbitrary
# whenever two filings share a date, which makes every downstream
# percentage non-reproducible across builds.
_VERSIONS = """
WITH ordered AS (
    SELECT cik, canonical_field, period_start, period_end, period_type,
           unit, value, filed_date, accession,
           first_value(value) OVER w AS first_value,
           first_value(filed_date) OVER w AS orig_filed,
           first_value(accession) OVER w AS orig_accession,
           max(value) OVER wf AS filing_max,
           min(value) OVER wf AS filing_min,
           count(*) OVER (
               PARTITION BY cik, canonical_field, period_start, period_end,
                            period_type, unit
           ) AS n_versions
    FROM fact
    WINDOW
      w AS (PARTITION BY cik, canonical_field, period_start, period_end,
                         period_type, unit
            ORDER BY filed_date, accession, value, fact_id),
      wf AS (PARTITION BY cik, canonical_field, period_start, period_end,
                          period_type, unit, filed_date, accession)
),
figures AS (
    SELECT cik, canonical_field, period_start, period_end, period_type,
           unit, any_value(first_value) AS original,
           max(n_versions) AS n_versions,
           count(DISTINCT accession || '|' || CAST(filed_date AS VARCHAR))
               AS n_filings,
           max(CASE WHEN filing_max <> filing_min THEN 1 ELSE 0 END)
               AS ambiguous,
           max(CASE WHEN (filed_date <> orig_filed
                          OR accession <> orig_accession)
                         AND first_value <> 0
                    THEN abs(value - first_value) / abs(first_value) * 100
                    ELSE 0 END) AS abs_pct_change
    FROM ordered
    GROUP BY cik, canonical_field, period_start, period_end, period_type, unit
)
"""


def restatement_stats(con: duckdb.DuckDBPyConnection) -> dict:
    """How often does a later filing materially change an earlier figure?

    `restated_figures` counts only figures whose value changed *between*
    filings, identified by distinct (filed_date, accession) pairs. Two
    values for one figure inside a single filing are not a restatement —
    nobody restated anything — and are counted in `ambiguous_figures`
    instead. Keeping them apart is the point: folding within-filing
    disagreement into the restatement rate reports a curation defect as a
    filer behaviour, and the headline number then measures the pipeline
    rather than the market.
    """
    row = con.execute(
        _VERSIONS + """
        SELECT count(*),
               count(*) FILTER (WHERE abs_pct_change > 0.0001),
               median(abs_pct_change) FILTER (WHERE abs_pct_change > 0.0001),
               max(abs_pct_change),
               count(*) FILTER (WHERE ambiguous = 1),
               count(*) FILTER (WHERE n_filings > 1)
        FROM figures
        """
    ).fetchone()
    total, restated, med, mx, ambiguous, multi_filed = row
    return {
        "total_figures": total,
        "restated_figures": restated,
        "restatement_rate": (restated / total) if total else 0.0,
        "median_abs_pct_change": med or 0.0,
        "max_abs_pct_change": mx or 0.0,
        "ambiguous_figures": ambiguous,
        "multi_filing_figures": multi_filed,
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
