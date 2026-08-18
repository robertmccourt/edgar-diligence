from datetime import date
from edgar.db import connect, init_schema
from edgar.curate.mapping import create_mapping_table, seed_mapping_rules
from edgar.curate.facts import create_fact_table
from edgar.query.coverage import coverage_map, FieldStatus

def _db(tmp_path):
    con = connect(tmp_path / "t.duckdb")
    init_schema(con); create_mapping_table(con); seed_mapping_rules(con)
    create_fact_table(con)
    con.execute("""INSERT INTO raw_sub VALUES
        ('a1','1','CO','3571','1231','10-K','20231231','2023','FY','20240215','0','1','1','2024q1')""")
    return con

def test_mapped_and_filed_is_available(tmp_path):
    con = _db(tmp_path)
    con.execute("""INSERT INTO fact VALUES
        ('f1',1,'revenue',100.0,'USD','duration','2023-01-01','2023-12-31',
         '2023','FY','2024-02-15','a1','Revenues','MR-0003',1.0,'2024q1')""")
    con.execute("""INSERT INTO raw_num VALUES
        ('a1','Revenues','us-gaap/2023','','20231231','4','USD','100','','','2024q1')""")
    m = coverage_map(con, 1, date(2023, 12, 31), date(2024, 6, 1))
    assert m["revenue"] == FieldStatus.AVAILABLE

def test_filed_after_as_of_is_not_yet_filed(tmp_path):
    con = _db(tmp_path)
    con.execute("""INSERT INTO fact VALUES
        ('f1',1,'revenue',100.0,'USD','duration','2023-01-01','2023-12-31',
         '2023','FY','2024-02-15','a1','Revenues','MR-0003',1.0,'2024q1')""")
    m = coverage_map(con, 1, date(2023, 12, 31), date(2024, 1, 10))
    assert m["revenue"] == FieldStatus.NOT_YET_FILED

def test_unmapped_when_related_tag_present_but_no_rule(tmp_path):
    con = _db(tmp_path)
    con.execute("""INSERT INTO raw_num VALUES
        ('a1','AcmeCostOfProductRevenue','acme/2023','','20231231','4','USD','60','','','2024q1')""")
    m = coverage_map(con, 1, date(2023, 12, 31), date(2024, 6, 1))
    assert m["cost_of_revenue"] == FieldStatus.UNMAPPED

def test_not_disclosed_when_nothing_resembles_the_concept(tmp_path):
    con = _db(tmp_path)
    con.execute("""INSERT INTO raw_num VALUES
        ('a1','Assets','us-gaap/2023','','20231231','0','USD','500','','','2024q1')""")
    m = coverage_map(con, 1, date(2023, 12, 31), date(2024, 6, 1))
    assert m["cost_of_revenue"] == FieldStatus.NOT_DISCLOSED

def test_conflicting_tags_for_same_field_are_ambiguous(tmp_path):
    con = _db(tmp_path)
    con.executemany(
        "INSERT INTO fact VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [("f1",1,"revenue",100.0,"USD","duration",date(2023,1,1),
          date(2023,12,31),"2023","FY",date(2024,2,15),"a1","Revenues",
          "MR-0003",1.0,"2024q1"),
         ("f2",1,"revenue",118.0,"USD","duration",date(2023,1,1),
          date(2023,12,31),"2023","FY",date(2024,2,15),"a1",
          "RevenueFromContractWithCustomerIncludingAssessedTax",
          "MR-0002",1.0,"2024q1")])
    m = coverage_map(con, 1, date(2023, 12, 31), date(2024, 6, 1))
    assert m["revenue"] == FieldStatus.AMBIGUOUS

def test_every_canonical_field_gets_a_status(tmp_path):
    con = _db(tmp_path)
    m = coverage_map(con, 1, date(2023, 12, 31), date(2024, 6, 1))
    assert len(m) == 10

def test_component_asset_tag_alone_is_not_disclosed_not_unmapped(tmp_path):
    con = _db(tmp_path)
    con.execute("""INSERT INTO raw_num VALUES
        ('a1','IntangibleAssetsNetExcludingGoodwill','us-gaap/2023','','20231231','0','USD','40','','','2024q1')""")
    m = coverage_map(con, 1, date(2023, 12, 31), date(2024, 6, 1))
    assert m["total_assets"] == FieldStatus.NOT_DISCLOSED

def test_custom_aggregate_asset_tag_is_still_unmapped(tmp_path):
    con = _db(tmp_path)
    con.execute("""INSERT INTO raw_num VALUES
        ('a1','AcmeTotalAssets','acme/2023','','20231231','0','USD','900','','','2024q1')""")
    m = coverage_map(con, 1, date(2023, 12, 31), date(2024, 6, 1))
    assert m["total_assets"] == FieldStatus.UNMAPPED

def test_unmapped_tag_from_a_different_period_does_not_leak_in(tmp_path):
    con = _db(tmp_path)
    # Filing for an earlier period (2022-12-31) reports a related-but-unmapped
    # tag. It must not be visible when scoring the 2023-12-31 period, whose
    # own filing never reported anything resembling cost_of_revenue.
    con.execute("""INSERT INTO raw_sub VALUES
        ('a0','1','CO','3571','1231','10-K','20221231','2022','FY','20230215','0','1','1','2023q1')""")
    con.execute("""INSERT INTO raw_num VALUES
        ('a0','AcmeCostOfProductRevenue','acme/2022','','20221231','4','USD','55','','','2023q1')""")
    m = coverage_map(con, 1, date(2023, 12, 31), date(2024, 6, 1))
    assert m["cost_of_revenue"] == FieldStatus.NOT_DISCLOSED
