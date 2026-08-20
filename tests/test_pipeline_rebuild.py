from datetime import date
from edgar.db import connect, init_schema
from edgar.pipeline import rebuild_curated

def _seed_raw(con):
    init_schema(con)
    con.execute("INSERT INTO raw_sub VALUES ('a-1','1','ACME','3571','1231',"
                "'10-Q','20230331','2023','Q1','20230501','0','1','1','2023q2')")
    # An instant fact under a NEW tag: proves rebuild picks up new rules.
    con.execute("INSERT INTO raw_num VALUES ('a-1','InventoryNet','us-gaap/2023',"
                "NULL,'20230331','0','USD','500',NULL,NULL,'2023q2')")

def test_rebuild_creates_facts_for_new_fields(tmp_path):
    con = connect(tmp_path / "t.duckdb")
    _seed_raw(con)
    report = rebuild_curated(con)
    row = con.execute("SELECT canonical_field, value, period_type, period_start "
                      "FROM fact").fetchone()
    assert row[0] == "inventory" and row[1] == 500.0
    assert row[2] == "instant" and row[3] is None
    assert report["facts"] >= 1 and "eligibility" in report

def test_rebuild_is_idempotent(tmp_path):
    con = connect(tmp_path / "t.duckdb")
    _seed_raw(con)
    rebuild_curated(con)
    n1 = con.execute("SELECT count(*) FROM fact").fetchone()[0]
    rebuild_curated(con)
    assert con.execute("SELECT count(*) FROM fact").fetchone()[0] == n1
