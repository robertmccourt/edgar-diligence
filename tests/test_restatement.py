from datetime import date
from edgar.db import connect, init_schema
from edgar.curate.facts import create_fact_table
from edgar.analysis.restatement import (
    restatement_stats, filing_lag_stats, restatement_detail)

def _f(fid, cik, field, val, pe, filed):
    return (fid, cik, field, val, "USD", "duration",
            None, pe, "2023", "Q1", filed, f"acc-{fid}", "Revenues",
            "MR-0003", 1.0, "q")

def _db(tmp_path, rows):
    con = connect(tmp_path / "t.duckdb"); create_fact_table(con)
    con.executemany(
        "INSERT INTO fact VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    return con

def test_no_restatement_when_single_filing(tmp_path):
    con = _db(tmp_path, [_f("a", 1, "revenue", 100.0,
                            date(2023, 3, 31), date(2023, 5, 10))])
    s = restatement_stats(con)
    assert s["total_figures"] == 1
    assert s["restated_figures"] == 0
    assert s["restatement_rate"] == 0.0

def test_detects_restatement_and_magnitude(tmp_path):
    con = _db(tmp_path, [
        _f("a", 1, "revenue", 100.0, date(2023, 3, 31), date(2023, 5, 10)),
        _f("b", 1, "revenue", 94.0, date(2023, 3, 31), date(2023, 11, 8)),
    ])
    s = restatement_stats(con)
    assert s["restated_figures"] == 1
    assert s["restatement_rate"] == 1.0
    assert abs(s["median_abs_pct_change"] - 6.0) < 1e-9

def test_identical_revalue_is_not_a_restatement(tmp_path):
    con = _db(tmp_path, [
        _f("a", 1, "revenue", 100.0, date(2023, 3, 31), date(2023, 5, 10)),
        _f("b", 1, "revenue", 100.0, date(2023, 3, 31), date(2023, 11, 8)),
    ])
    assert restatement_stats(con)["restated_figures"] == 0

# The former `test_filing_lag` measured the lag from `fact` rows and so
# asserted the very premise this metric was corrected to abandon: that a
# fact's (cik, canonical_field, period_end) identifies a filing. It does
# not — one filing emits facts for its own period and for the prior-year
# comparatives it restates, which is half of why the measured median was
# 397 days. It is superseded by the raw_sub tests below, which assert the
# same properties (n, median, p90) against the source that can answer the
# question.

def test_detail_filters_by_magnitude(tmp_path):
    con = _db(tmp_path, [
        _f("a", 1, "revenue", 100.0, date(2023, 3, 31), date(2023, 5, 10)),
        _f("b", 1, "revenue", 94.0, date(2023, 3, 31), date(2023, 11, 8)),
    ])
    assert len(restatement_detail(con, min_abs_pct=1.0)) == 1
    assert len(restatement_detail(con, min_abs_pct=10.0)) == 0


def _fp(fid, cik, field, val, ps, pe, filed):
    """A fact carrying an explicit period_start, so period length matters."""
    return (fid, cik, field, val, "USD", "duration",
            ps, pe, "2023", "Q1", filed, f"acc-{fid}", "Revenues",
            "MR-0003", 1.0, "q")


def test_quarterly_and_ytd_ending_same_day_are_not_a_restatement(tmp_path):
    """A three-month figure and a year-to-date figure that end on the same
    day are different economic quantities, not two versions of one. Before
    period_start entered the grouping key, this pair was scored as a
    restatement with a 105.6% change — which is what inflated the measured
    restatement rate and produced a 54.2% median absolute change.

    Modelled on cik=1536089, net_income, period_end 2023-03-31.
    """
    con = _db(tmp_path, [
        _fp("a", 1, "net_income", -840746.0, date(2023, 1, 1),
            date(2023, 3, 31), date(2024, 4, 16)),
        _fp("b", 1, "net_income", -1728362.0, date(2022, 10, 1),
            date(2023, 3, 31), date(2024, 4, 16)),
    ])
    s = restatement_stats(con)
    assert s["total_figures"] == 2      # two figures, not one
    assert s["restated_figures"] == 0
    assert s["restatement_rate"] == 0.0
    assert s["max_abs_pct_change"] == 0.0
    assert restatement_detail(con) == []


def test_three_period_lengths_ending_same_day_are_three_figures(tmp_path):
    con = _db(tmp_path, [
        _fp("a", 1, "net_income", -840746.0, date(2023, 1, 1),
            date(2023, 3, 31), date(2024, 6, 7)),
        _fp("b", 1, "net_income", -1728362.0, date(2022, 10, 1),
            date(2023, 3, 31), date(2024, 4, 16)),
        _fp("c", 1, "net_income", -1728362.0, date(2022, 4, 1),
            date(2023, 3, 31), date(2024, 4, 16)),
    ])
    s = restatement_stats(con)
    assert s["total_figures"] == 3
    assert s["restated_figures"] == 0


def test_genuine_restatement_of_one_period_is_still_counted(tmp_path):
    """Same period_start AND period_end, two accessions, two filing dates,
    different values — a real restatement, which must survive the fix."""
    con = _db(tmp_path, [
        _fp("a", 1, "net_income", -840746.0, date(2023, 1, 1),
            date(2023, 3, 31), date(2024, 4, 16)),
        _fp("b", 1, "net_income", -882783.3, date(2023, 1, 1),
            date(2023, 3, 31), date(2024, 6, 7)),
        # a co-filed YTD figure must not disturb the measurement
        _fp("c", 1, "net_income", -1728362.0, date(2022, 10, 1),
            date(2023, 3, 31), date(2024, 6, 7)),
    ])
    s = restatement_stats(con)
    assert s["total_figures"] == 2
    assert s["restated_figures"] == 1
    assert abs(s["median_abs_pct_change"] - 5.0) < 1e-6
    detail = restatement_detail(con, min_abs_pct=1.0)
    assert len(detail) == 1
    assert detail[0][2] == date(2023, 1, 1)   # period_start of the figure
    assert detail[0][3] == date(2023, 3, 31)


def _sub(adsh, form, period, filed):
    return (adsh, 1, "CO", "3571", "1231", form, period, "2023", "FY",
            filed, "0", "1", "1", "2024q1")


def _sub_db(tmp_path, subs, facts=()):
    con = connect(tmp_path / "t.duckdb")
    init_schema(con)
    create_fact_table(con)
    con.executemany(
        "INSERT INTO raw_sub VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", subs)
    if facts:
        con.executemany(
            "INSERT INTO fact VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", facts)
    return con


def test_filing_lag_measured_per_filing_from_raw_sub(tmp_path):
    """Lag is 'how long after its own period ended did this company file?'.
    raw_sub answers that directly: one row per filing, carrying both the
    period it covers and the date it was filed."""
    con = _sub_db(tmp_path, [
        _sub("a1", "10-Q", "20231231", "20240209"),   # 40 days
        _sub("a2", "10-K", "20231231", "20240214"),   # 45 days
        _sub("a3", "10-Q", "20231231", "20240219"),   # 50 days
    ])
    lag = filing_lag_stats(con)
    assert lag["n"] == 3
    assert lag["median_days"] == 45
    assert abs(lag["p90_days"] - 49.0) < 1e-9


def test_filing_lag_excludes_blank_period_rows(tmp_path):
    """raw_sub.period is blank for some filers. Those rows carry no period
    to measure from and must be dropped, not folded in as a zero or null
    that poisons the median."""
    con = _sub_db(tmp_path, [
        _sub("a1", "10-Q", "20231231", "20240209"),   # 40 days
        _sub("a2", "10-K", "20231231", "20240214"),   # 45 days
        _sub("a3", "10-Q", "20231231", "20240219"),   # 50 days
        _sub("a4", "10-K", "", "20240301"),           # blank period
        _sub("a5", "10-Q", "   ", "20240301"),        # whitespace period
    ])
    lag = filing_lag_stats(con)
    assert lag["n"] == 3
    assert lag["median_days"] == 45


def test_filing_lag_counts_only_10k_and_10q(tmp_path):
    con = _sub_db(tmp_path, [
        _sub("a1", "10-Q", "20231231", "20240209"),   # 40 days
        _sub("a2", "10-K", "20231231", "20240214"),   # 45 days
        _sub("a3", "10-Q", "20231231", "20240219"),   # 50 days
        _sub("a4", "S-1", "20200101", "20240301"),    # registration, not a report
        _sub("a5", "8-K", "20231231", "20240102"),    # current report
    ])
    lag = filing_lag_stats(con)
    assert lag["n"] == 3
    assert lag["median_days"] == 45


def test_filing_lag_ignores_prior_year_comparatives_in_fact(tmp_path):
    """The old implementation measured from `fact`, where a single filing
    also carries prior-year comparative figures — so a 10-K filed 45 days
    after its own year end was scored as a ~410-day lag for the comparative
    period it restated. Those facts must not move the answer."""
    comparative = _fp("z", 1, "revenue", 100.0, date(2022, 1, 1),
                      date(2022, 12, 31), date(2024, 2, 14))
    own = _fp("y", 1, "revenue", 120.0, date(2023, 1, 1),
              date(2023, 12, 31), date(2024, 2, 14))
    con = _sub_db(tmp_path,
                  [_sub("a2", "10-K", "20231231", "20240214")],
                  facts=[comparative, own])
    lag = filing_lag_stats(con)
    assert lag["n"] == 1
    assert lag["median_days"] == 45


def _fu(fid, cik, field, val, unit, ps, pe, filed, acc=None):
    """A fact carrying an explicit unit, so currency matters."""
    return (fid, cik, field, val, unit, "duration",
            ps, pe, "2023", "Q1", filed, acc or f"acc-{fid}", "Revenues",
            "MR-0003", 1.0, "q")


def test_two_currencies_are_not_a_restatement(tmp_path):
    """The same figure filed in USD and CNY differs by the exchange rate,
    not by a correction. Keyed without `unit` the pair is scored as a
    restatement of ~537%. Modelled on cik=1296774, total_assets."""
    con = _db(tmp_path, [
        _fu("a", 1, "total_assets", 205617546.0, "USD", None,
            date(2023, 12, 31), date(2024, 3, 15), acc="acc-1"),
        _fu("b", 1, "total_assets", 1310318375.0, "CNY", None,
            date(2023, 12, 31), date(2024, 3, 15), acc="acc-1"),
    ])
    s = restatement_stats(con)
    assert s["total_figures"] == 2      # two figures, not one
    assert s["restated_figures"] == 0
    assert s["max_abs_pct_change"] == 0.0
