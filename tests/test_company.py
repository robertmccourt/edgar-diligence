from edgar.db import connect, init_schema
from edgar.curate.company import sic_to_sector, fye_to_month, build_company_table

def test_sic_to_sector_financials():
    assert sic_to_sector("6021") == "financials"
    assert sic_to_sector("6798") == "financials"

def test_sic_to_sector_manufacturing_and_utilities():
    assert sic_to_sector("3571") == "manufacturing"
    assert sic_to_sector("4911") == "utilities"

def test_sic_to_sector_handles_missing():
    assert sic_to_sector(None) == "unknown"
    assert sic_to_sector("") == "unknown"

def test_fye_to_month():
    assert fye_to_month("0930") == 9
    assert fye_to_month("1231") == 12
    assert fye_to_month("") is None
    assert fye_to_month("bad") is None

def test_build_company_table(tmp_path):
    con = connect(tmp_path / "t.duckdb"); init_schema(con)
    con.execute("""INSERT INTO raw_sub VALUES
        ('a1','320193','APPLE INC','3571','0930','10-K','20230930','2023','FY','20231103','0','1','1','2023q4'),
        ('a2','320193','APPLE INC','3571','0930','10-Q','20240630','2024','Q3','20240802','0','1','1','2024q3'),
        ('b1','19617','JPMORGAN','6021','1231','10-K','20231231','2023','FY','20240216','0','1','1','2024q1')""")
    n = build_company_table(con)
    assert n == 2
    row = con.execute(
        "SELECT sector, fiscal_year_end_month, first_filing_date "
        "FROM company WHERE cik = 320193").fetchone()
    assert row[0] == "manufacturing"
    assert row[1] == 9
    assert str(row[2]) == "2023-11-03"
    assert con.execute(
        "SELECT sector FROM company WHERE cik = 19617").fetchone()[0] == "financials"

def test_build_company_table_takes_latest_sic_and_sector(tmp_path):
    """Company with changed SIC must take latest SIC and sector."""
    con = connect(tmp_path / "t.duckdb"); init_schema(con)
    con.execute("""INSERT INTO raw_sub VALUES
        ('x1','55555','OLD NAME','3571','0630','10-K','20220630','2022','FY','20220731','0','1','1','2022q2'),
        ('x2','55555','CHANGED NAME','7372','0930','10-K','20230630','2023','FY','20230731','0','1','1','2023q2'),
        ('x3','55555','LATEST NAME','7372','0930','10-K','20240630','2024','FY','20240731','0','1','1','2024q2')""")
    build_company_table(con)
    row = con.execute(
        "SELECT sic, sector, first_filing_date FROM company WHERE cik = 55555"
    ).fetchone()
    assert row[0] == "7372"
    assert row[1] == "services"
    assert str(row[2]) == "2022-07-31"

def test_build_company_table_takes_latest_fye(tmp_path):
    """Company with changed fiscal year end must take latest FYE."""
    con = connect(tmp_path / "t.duckdb"); init_schema(con)
    con.execute("""INSERT INTO raw_sub VALUES
        ('y1','66666','COMPANY','5000','1231','10-K','20210630','2021','FY','20210731','0','1','1','2021q2'),
        ('y2','66666','COMPANY','5000','0930','10-K','20220630','2022','FY','20220731','0','1','1','2022q2'),
        ('y3','66666','COMPANY','5000','0930','10-K','20230630','2023','FY','20230731','0','1','1','2023q2')""")
    build_company_table(con)
    row = con.execute(
        "SELECT fiscal_year_end_month, first_filing_date FROM company WHERE cik = 66666"
    ).fetchone()
    assert row[0] == 9
    assert str(row[1]) == "2021-07-31"

def test_build_company_table_tiebreak_determinism(tmp_path):
    """Same filing date requires deterministic tiebreak on adsh descending."""
    con = connect(tmp_path / "t.duckdb"); init_schema(con)
    # Two rows with same filing date (20240630) but different adsh
    con.execute("""INSERT INTO raw_sub VALUES
        ('zz1','77777','COMPANY A','5000','1231','10-K','20240630','2024','FY','20240630','0','1','1','2024q2'),
        ('zz2','77777','COMPANY B','5000','0930','10-K','20240630','2024','FY','20240630','0','1','1','2024q2')""")
    build_company_table(con)
    row1 = con.execute(
        "SELECT name, fiscal_year_end_month FROM company WHERE cik = 77777"
    ).fetchone()

    # Run again to verify determinism
    con.execute("DELETE FROM company")
    build_company_table(con)
    row2 = con.execute(
        "SELECT name, fiscal_year_end_month FROM company WHERE cik = 77777"
    ).fetchone()

    assert row1 == row2
    # Should take the one with higher adsh (zz2 > zz1)
    assert row1[0] == "COMPANY B"
    assert row1[1] == 9
