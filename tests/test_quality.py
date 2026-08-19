from datetime import date
from edgar.db import connect
from edgar.curate.facts import create_fact_table
from edgar.quality.checks import run_quality_checks

def _f(fid, cik, field, val, ptype, pstart, pe, filed):
    return (fid, cik, field, val, "USD", ptype, pstart, pe,
            "2023", "Q1", filed, "acc", "Revenues", "MR-0003", 1.0, "q")

def _db(tmp_path, rows):
    con = connect(tmp_path / "t.duckdb"); create_fact_table(con)
    con.executemany(
        "INSERT INTO fact VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    return con

def test_clean_data_passes_all_checks(tmp_path):
    con = _db(tmp_path, [
        _f("a", 1, "revenue", 100.0, "duration",
           date(2023, 1, 1), date(2023, 3, 31), date(2023, 5, 10)),
        _f("b", 1, "total_assets", 500.0, "instant",
           None, date(2023, 3, 31), date(2023, 5, 10)),
    ])
    results = run_quality_checks(con)
    failed = [r.name for r in results if not r.passed]
    assert failed == []

def test_detects_filed_before_period_end(tmp_path):
    con = _db(tmp_path, [
        _f("a", 1, "revenue", 100.0, "duration",
           date(2023, 1, 1), date(2023, 3, 31), date(2023, 1, 15)),
    ])
    r = next(x for x in run_quality_checks(con) if x.name == "filed_after_period")
    assert not r.passed

def test_detects_duration_without_start(tmp_path):
    con = _db(tmp_path, [
        _f("a", 1, "revenue", 100.0, "duration",
           None, date(2023, 3, 31), date(2023, 5, 10)),
    ])
    r = next(x for x in run_quality_checks(con) if x.name == "duration_has_start")
    assert not r.passed

def test_detects_instant_with_start(tmp_path):
    con = _db(tmp_path, [
        _f("a", 1, "total_assets", 5.0, "instant",
           date(2023, 1, 1), date(2023, 3, 31), date(2023, 5, 10)),
    ])
    r = next(x for x in run_quality_checks(con) if x.name == "instant_has_no_start")
    assert not r.passed

def test_every_check_reports_threshold(tmp_path):
    con = _db(tmp_path, [
        _f("a", 1, "revenue", 100.0, "duration",
           date(2023, 1, 1), date(2023, 3, 31), date(2023, 5, 10)),
    ])
    for r in run_quality_checks(con):
        assert r.threshold is not None and r.detail
