from datetime import date
from edgar.db import connect
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

def test_filing_lag(tmp_path):
    con = _db(tmp_path, [
        _f("a", 1, "revenue", 100.0, date(2023, 3, 31), date(2023, 5, 10)),
        _f("b", 2, "revenue", 100.0, date(2023, 3, 31), date(2023, 5, 20)),
    ])
    lag = filing_lag_stats(con)
    assert lag["n"] == 2
    assert 40 <= lag["median_days"] <= 50

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
