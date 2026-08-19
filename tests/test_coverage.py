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

def test_members_equity_alone_is_unmapped_for_stockholders_equity(tmp_path):
    con = _db(tmp_path)
    con.execute("""INSERT INTO raw_num VALUES
        ('a1','MembersEquity','acme/2023','','20231231','0','USD','700','','','2024q1')""")
    m = coverage_map(con, 1, date(2023, 12, 31), date(2024, 6, 1))
    assert m["stockholders_equity"] == FieldStatus.UNMAPPED

def test_equity_method_investments_is_still_not_disclosed(tmp_path):
    con = _db(tmp_path)
    con.execute("""INSERT INTO raw_num VALUES
        ('a1','EquityMethodInvestments','us-gaap/2023','','20231231','0','USD','25','','','2024q1')""")
    m = coverage_map(con, 1, date(2023, 12, 31), date(2024, 6, 1))
    assert m["stockholders_equity"] == FieldStatus.NOT_DISCLOSED

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


def test_quarterly_and_ytd_filed_same_day_are_not_ambiguous(tmp_path):
    """AMBIGUOUS means two mapped tags claim the same canonical field with
    irreconcilable values for the same figure. A three-month figure and the
    year-to-date figure ending the same day are not the same figure — every
    10-Q files both, from the same tag, on the same day, with legitimately
    different values. Without period_start in the grouping key this fires on
    the ordinary shape of a quarterly report."""
    con = _db(tmp_path)
    con.executemany(
        "INSERT INTO fact VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [("f1",1,"net_income",-840746.0,"USD","duration",date(2023,10,1),
          date(2023,12,31),"2023","Q4",date(2024,2,15),"a1","NetIncomeLoss",
          "MR-0005",1.0,"2024q1"),
         ("f2",1,"net_income",-1728362.0,"USD","duration",date(2023,1,1),
          date(2023,12,31),"2023","FY",date(2024,2,15),"a1","NetIncomeLoss",
          "MR-0005",1.0,"2024q1")])
    m = coverage_map(con, 1, date(2023, 12, 31), date(2024, 6, 1))
    assert m["net_income"] == FieldStatus.AVAILABLE


def test_conflicting_tags_for_same_period_length_are_still_ambiguous(tmp_path):
    """The genuine case must survive: same period_start and period_end, same
    filing, two mapped tags, two values."""
    con = _db(tmp_path)
    con.executemany(
        "INSERT INTO fact VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [("f1",1,"net_income",-840746.0,"USD","duration",date(2023,1,1),
          date(2023,12,31),"2023","FY",date(2024,2,15),"a1","NetIncomeLoss",
          "MR-0005",1.0,"2024q1"),
         ("f2",1,"net_income",-999999.0,"USD","duration",date(2023,1,1),
          date(2023,12,31),"2023","FY",date(2024,2,15),"a1","ProfitLoss",
          "MR-0006",1.0,"2024q1"),
         # a co-filed quarterly figure must not mask the real conflict
         ("f3",1,"net_income",-100000.0,"USD","duration",date(2023,10,1),
          date(2023,12,31),"2023","Q4",date(2024,2,15),"a1","NetIncomeLoss",
          "MR-0005",1.0,"2024q1")])
    m = coverage_map(con, 1, date(2023, 12, 31), date(2024, 6, 1))
    assert m["net_income"] == FieldStatus.AMBIGUOUS


def test_two_currencies_are_not_ambiguous(tmp_path):
    """A filer reporting the same line in USD and CNY has reported two
    figures whose values differ by the exchange rate. That is a reporting
    convention, not two mapped tags disagreeing, and must not be flagged
    AMBIGUOUS. Modelled on cik=1296774."""
    con = _db(tmp_path)
    con.execute("""INSERT INTO fact VALUES
        ('f1',1,'total_assets',205617546.0,'USD','instant',NULL,'2023-12-31',
         '2023','FY','2024-02-15','a1','Assets','MR-0012',1.0,'2024q1'),
        ('f2',1,'total_assets',1310318375.0,'CNY','instant',NULL,'2023-12-31',
         '2023','FY','2024-02-15','a1','Assets','MR-0012',1.0,'2024q1')""")
    m = coverage_map(con, 1, date(2023, 12, 31), date(2024, 6, 1))
    assert m["total_assets"] == FieldStatus.AVAILABLE
