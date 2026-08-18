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
