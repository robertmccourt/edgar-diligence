# tests/test_asof.py
from datetime import date
from edgar.db import connect
from edgar.curate.facts import create_fact_table
from edgar.query.asof import get_facts_asof, restatement_history

def _restated(tmp_path):
    """Revenue for Q1 2023 first reported as 100, later restated to 94."""
    con = connect(tmp_path / "t.duckdb")
    create_fact_table(con)
    con.executemany(
        "INSERT INTO fact VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            ("f1", 1, "revenue", 100.0, "USD", "duration",
             date(2023, 1, 1), date(2023, 3, 31), "2023", "Q1",
             date(2023, 5, 10), "acc-1", "Revenues", "MR-0003", 1.0, "2023q2"),
            ("f2", 1, "revenue", 94.0, "USD", "duration",
             date(2023, 1, 1), date(2023, 3, 31), "2023", "Q1",
             date(2023, 11, 8), "acc-2", "Revenues", "MR-0003", 1.0, "2023q4"),
        ],
    )
    return con

def test_before_first_filing_returns_nothing(tmp_path):
    con = _restated(tmp_path)
    assert get_facts_asof(con, 1, ["revenue"],
                          date(2023, 1, 1), date(2023, 12, 31),
                          date(2023, 4, 30)) == []

def test_between_filings_returns_original_value(tmp_path):
    con = _restated(tmp_path)
    got = get_facts_asof(con, 1, ["revenue"], date(2023, 1, 1),
                         date(2023, 12, 31), date(2023, 9, 1))
    assert len(got) == 1 and got[0].value == 100.0
    assert got[0].accession == "acc-1"

def test_after_restatement_returns_corrected_value(tmp_path):
    con = _restated(tmp_path)
    got = get_facts_asof(con, 1, ["revenue"], date(2023, 1, 1),
                         date(2023, 12, 31), date(2024, 1, 1))
    assert len(got) == 1 and got[0].value == 94.0
    assert got[0].accession == "acc-2"

def test_as_of_equals_filed_date_is_inclusive(tmp_path):
    con = _restated(tmp_path)
    got = get_facts_asof(con, 1, ["revenue"], date(2023, 1, 1),
                         date(2023, 12, 31), date(2023, 5, 10))
    assert got[0].value == 100.0

def test_restatement_history_returns_both_versions(tmp_path):
    con = _restated(tmp_path)
    hist = restatement_history(con, 1, "revenue", date(2023, 3, 31))
    assert [h.value for h in hist] == [100.0, 94.0]

def test_unknown_field_returns_empty(tmp_path):
    con = _restated(tmp_path)
    assert get_facts_asof(con, 1, ["capex"], date(2023, 1, 1),
                          date(2023, 12, 31), date(2024, 1, 1)) == []
