from edgar.db import connect
from edgar.curate.facts import create_fact_table
from edgar.curate.universe import apply_eligibility

def _db(tmp_path):
    con = connect(tmp_path / "t.duckdb")
    create_fact_table(con)
    con.execute("""CREATE TABLE company (
        cik BIGINT, name VARCHAR, sic VARCHAR, sector VARCHAR,
        fiscal_year_end_month INTEGER, first_filing_date DATE,
        eligibility_status VARCHAR, exclusion_reason VARCHAR)""")
    con.execute("""INSERT INTO company VALUES
        (1,'CLEAN','3571','manufacturing',12,'2019-01-01','pending',NULL),
        (2,'BANK','6021','financials',12,'2019-01-01','pending',NULL),
        (3,'POWER','4911','utilities',12,'2019-01-01','pending',NULL),
        (4,'YOUNG','3571','manufacturing',12,'2024-01-01','pending',NULL),
        (5,'SPARSE','3571','manufacturing',12,'2019-01-01','pending',NULL)""")
    fields = ["revenue","net_income","total_assets","total_liabilities",
              "stockholders_equity","operating_cash_flow"]
    rows = []
    for q in range(14):  # 14 distinct period_ends
        pe = f"20{19 + q // 4}-{3 * (q % 4) + 1:02d}-28"
        for f in fields:
            rows.append((f"c1-{q}-{f}", 1, f, 1.0, "USD", "duration",
                         None, pe, "2020", "Q1", pe, "a", "T", "MR-0001",
                         1.0, "x"))
        rows.append((f"c5-{q}", 5, "revenue", 1.0, "USD", "duration",
                     None, pe, "2020", "Q1", pe, "a", "T", "MR-0001", 1.0, "x"))
        rows.append((f"c4-{q}", 4, "revenue", 1.0, "USD", "duration",
                     None, pe, "2020", "Q1", pe, "a", "T", "MR-0001", 1.0, "x"))
    con.executemany(
        "INSERT INTO fact VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    return con

def test_clean_company_is_eligible(tmp_path):
    con = _db(tmp_path); apply_eligibility(con)
    assert con.execute(
        "SELECT eligibility_status FROM company WHERE cik=1").fetchone()[0] == "eligible"

def test_financials_and_utilities_excluded(tmp_path):
    con = _db(tmp_path); apply_eligibility(con)
    for cik in (2, 3):
        status, reason = con.execute(
            "SELECT eligibility_status, exclusion_reason FROM company WHERE cik=?",
            [cik]).fetchone()
        assert status == "excluded" and reason == "excluded_sector"

def test_insufficient_field_coverage_excluded(tmp_path):
    con = _db(tmp_path); apply_eligibility(con)
    status, reason = con.execute(
        "SELECT eligibility_status, exclusion_reason FROM company WHERE cik=5"
    ).fetchone()
    assert status == "excluded" and reason == "insufficient_field_coverage"

def test_returns_removal_counts(tmp_path):
    con = _db(tmp_path)
    counts = apply_eligibility(con)
    assert counts["excluded_sector"] == 2
    assert counts["eligible"] == 1
