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
