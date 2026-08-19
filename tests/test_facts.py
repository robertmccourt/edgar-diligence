from edgar.db import connect, init_schema
from edgar.curate.mapping import create_mapping_table, seed_mapping_rules
from edgar.curate.facts import make_fact_id, create_fact_table, build_facts

def _seed(con):
    init_schema(con); create_mapping_table(con); seed_mapping_rules(con)
    con.execute("""INSERT INTO raw_sub VALUES
        ('a1','320193','APPLE','3571','0930','10-Q','20240630','2024','Q3','20240802','0','1','1','2024q3')""")
    con.execute("""INSERT INTO raw_num VALUES
        ('a1','Revenues','us-gaap/2024','','20240630','1','USD','85777000000','','','2024q3'),
        ('a1','Assets','us-gaap/2024','','20240630','0','USD','331612000000','','','2024q3'),
        ('a1','SomeCustomTag','apple/2024','','20240630','1','USD','123','','','2024q3')""")

def test_fact_id_is_deterministic():
    a = make_fact_id("a1", "Revenues", "20240630", "1", "USD", "")
    b = make_fact_id("a1", "Revenues", "20240630", "1", "USD", "")
    assert a == b and len(a) == 16

def test_fact_id_differs_by_period():
    assert make_fact_id("a1", "Revenues", "20240630", "1", "USD", "") != \
           make_fact_id("a1", "Revenues", "20240331", "1", "USD", "")

def test_build_facts_maps_known_tags_only(tmp_path):
    con = connect(tmp_path / "t.duckdb"); _seed(con)
    create_fact_table(con)
    n = build_facts(con)
    assert n == 2  # custom tag is not mapped
    fields = {r[0] for r in con.execute(
        "SELECT canonical_field FROM fact").fetchall()}
    assert fields == {"revenue", "total_assets"}

def test_build_facts_sets_period_type_and_filed_date(tmp_path):
    con = connect(tmp_path / "t.duckdb"); _seed(con)
    create_fact_table(con); build_facts(con)
    row = con.execute(
        "SELECT period_type, period_start, period_end, filed_date "
        "FROM fact WHERE canonical_field = 'revenue'").fetchone()
    assert row[0] == "duration"
    assert str(row[1]) == "2024-04-01"
    assert str(row[2]) == "2024-06-30"
    assert str(row[3]) == "2024-08-02"

def test_instant_fact_has_null_start(tmp_path):
    con = connect(tmp_path / "t.duckdb"); _seed(con)
    create_fact_table(con); build_facts(con)
    row = con.execute(
        "SELECT period_type, period_start FROM fact "
        "WHERE canonical_field = 'total_assets'").fetchone()
    assert row[0] == "instant" and row[1] is None


def test_segment_row_does_not_clobber_consolidated_value(tmp_path):
    con = connect(tmp_path / "t.duckdb")
    init_schema(con); create_mapping_table(con); seed_mapping_rules(con)
    con.execute("""INSERT INTO raw_sub VALUES
        ('a1','320193','APPLE','3571','0930','10-Q','20240630','2024','Q3','20240802','0','1','1','2024q3')""")
    con.execute("""INSERT INTO raw_num VALUES
        ('a1','Revenues','us-gaap/2024','','20240630','1','USD','100000000','','','2024q3'),
        ('a1','Revenues','us-gaap/2024','','20240630','1','USD','7000000','','BusinessSegments=Widgets;','2024q3')""")
    create_fact_table(con)
    n = build_facts(con)
    assert n == 1
    rows = con.execute(
        "SELECT value FROM fact WHERE canonical_field = 'revenue'").fetchall()
    assert len(rows) == 1
    assert rows[0][0] == 100000000.0


def test_null_segments_treated_as_consolidated(tmp_path):
    con = connect(tmp_path / "t.duckdb")
    init_schema(con); create_mapping_table(con); seed_mapping_rules(con)
    con.execute("""INSERT INTO raw_sub VALUES
        ('a1','320193','APPLE','3571','0930','10-Q','20240630','2024','Q3','20240802','0','1','1','2024q3')""")
    con.execute("""INSERT INTO raw_num VALUES
        ('a1','Assets','us-gaap/2024','','20240630','0','USD','331612000000',NULL,NULL,'2024q3')""")
    create_fact_table(con)
    n = build_facts(con)
    assert n == 1
    row = con.execute(
        "SELECT value FROM fact WHERE canonical_field = 'total_assets'").fetchone()
    assert row[0] == 331612000000.0


def test_segment_only_concept_yields_no_fact(tmp_path):
    con = connect(tmp_path / "t.duckdb")
    init_schema(con); create_mapping_table(con); seed_mapping_rules(con)
    con.execute("""INSERT INTO raw_sub VALUES
        ('a1','320193','APPLE','3571','0930','10-Q','20240630','2024','Q3','20240802','0','1','1','2024q3')""")
    con.execute("""INSERT INTO raw_num VALUES
        ('a1','Revenues','us-gaap/2024','','20240630','1','USD','7000000','','BusinessSegments=Widgets;','2024q3')""")
    create_fact_table(con)
    n = build_facts(con)
    assert n == 0
    rows = con.execute("SELECT * FROM fact").fetchall()
    assert rows == []


def test_restatement_preserved_as_second_row(tmp_path):
    con = connect(tmp_path / "t.duckdb")
    init_schema(con); create_mapping_table(con); seed_mapping_rules(con)
    con.execute("""INSERT INTO raw_sub VALUES
        ('a1','320193','APPLE','3571','0930','10-Q','20240630','2024','Q3','20240802','0','1','1','2024q3'),
        ('a2','320193','APPLE','3571','0930','10-Q/A','20240630','2024','Q3','20240915','0','1','1','2024q3')""")
    con.execute("""INSERT INTO raw_num VALUES
        ('a1','Revenues','us-gaap/2024','','20240630','1','USD','85777000000','','','2024q3'),
        ('a2','Revenues','us-gaap/2024','','20240630','1','USD','86000000000','','','2024q3')""")
    create_fact_table(con)
    n = build_facts(con)
    assert n == 2
    rows = con.execute(
        "SELECT fact_id, value, filed_date FROM fact "
        "WHERE canonical_field = 'revenue' ORDER BY filed_date").fetchall()
    assert len(rows) == 2
    fact_ids = {r[0] for r in rows}
    assert len(fact_ids) == 2
    values = {r[1] for r in rows}
    assert values == {85777000000.0, 86000000000.0}
    filed_dates = {str(r[2]) for r in rows}
    assert filed_dates == {"2024-08-02", "2024-09-15"}


def test_non_numeric_value_skipped_with_warning(tmp_path, capsys):
    con = connect(tmp_path / "t.duckdb"); _seed(con)
    con.execute("""INSERT INTO raw_sub VALUES
        ('a2','320193','APPLE','3571','0930','10-Q','20240930','2024','Q4','20241101','0','1','1','2024q3')""")
    con.execute("""INSERT INTO raw_num VALUES
        ('a2','Revenues','us-gaap/2024','','20240930','1','USD','not_a_number','','','2024q3')""")
    create_fact_table(con)
    n = build_facts(con)
    assert n == 2  # the two good facts from _seed still load
    fields = {r[0] for r in con.execute(
        "SELECT canonical_field FROM fact").fetchall()}
    assert fields == {"revenue", "total_assets"}
    out = capsys.readouterr().out
    assert "WARNING" in out
    assert "1" in out  # one row skipped


def test_malformed_filed_skipped_with_warning(tmp_path, capsys):
    con = connect(tmp_path / "t.duckdb"); _seed(con)
    con.execute("""INSERT INTO raw_sub VALUES
        ('a3','320193','APPLE','3571','0930','10-Q','20240930','2024','Q4','BADDATE','0','1','1','2024q3')""")
    con.execute("""INSERT INTO raw_num VALUES
        ('a3','Assets','us-gaap/2024','','20240930','0','USD','999000000','','','2024q3')""")
    create_fact_table(con)
    n = build_facts(con)
    assert n == 2  # the two good facts from _seed still load; a3 row skipped
    fields = {r[0] for r in con.execute(
        "SELECT canonical_field FROM fact").fetchall()}
    assert fields == {"revenue", "total_assets"}
    out = capsys.readouterr().out
    assert "WARNING" in out
    assert "1" in out  # one row skipped


def test_fact_filed_before_period_end_is_skipped(tmp_path, capsys):
    """A company cannot report actuals for a period that has not ended yet.
    Real example from the smoke run: a balance sheet dated 2024-12-31 filed
    2024-05-07. These are source-data errors, so they are dropped at build
    time — the filed_after_period quality check keeps a 0.0 threshold that
    means something rather than being relaxed to tolerate violations.
    """
    con = connect(tmp_path / "t.duckdb"); _seed(con)
    con.execute("""INSERT INTO raw_sub VALUES
        ('a4','320193','APPLE','3571','0930','10-Q','20241231','2025','Q1','20240507','0','1','1','2024q3')""")
    con.execute("""INSERT INTO raw_num VALUES
        ('a4','Assets','us-gaap/2024','','20241231','0','USD','400000000000','','','2024q3')""")
    create_fact_table(con)
    n = build_facts(con)
    assert n == 2  # the two good facts from _seed still load
    assert con.execute(
        "SELECT count(*) FROM fact WHERE filed_date < period_end"
    ).fetchone()[0] == 0
    assert con.execute(
        "SELECT count(*) FROM fact WHERE accession = 'a4'").fetchone()[0] == 0
    fields = {r[0] for r in con.execute(
        "SELECT canonical_field FROM fact").fetchall()}
    assert fields == {"revenue", "total_assets"}
    out = capsys.readouterr().out
    assert "WARNING" in out
    assert "1 of 3 rows skipped" in out


def test_fact_filed_on_period_end_is_kept(tmp_path):
    """Filing on the closing day itself is unusual but not impossible; only
    filed_date strictly before period_end is contradictory."""
    con = connect(tmp_path / "t.duckdb")
    init_schema(con); create_mapping_table(con); seed_mapping_rules(con)
    con.execute("""INSERT INTO raw_sub VALUES
        ('a5','320193','APPLE','3571','0930','10-Q','20240630','2024','Q3','20240630','0','1','1','2024q3')""")
    con.execute("""INSERT INTO raw_num VALUES
        ('a5','Assets','us-gaap/2024','','20240630','0','USD','331612000000','','','2024q3')""")
    create_fact_table(con)
    assert build_facts(con) == 1


def test_skipped_warning_separates_malformed_from_impossible(tmp_path, capsys):
    con = connect(tmp_path / "t.duckdb"); _seed(con)
    con.execute("""INSERT INTO raw_sub VALUES
        ('a4','320193','APPLE','3571','0930','10-Q','20241231','2025','Q1','20240507','0','1','1','2024q3'),
        ('a6','320193','APPLE','3571','0930','10-Q','20240930','2024','Q4','20241101','0','1','1','2024q3')""")
    con.execute("""INSERT INTO raw_num VALUES
        ('a4','Assets','us-gaap/2024','','20241231','0','USD','400000000000','','','2024q3'),
        ('a6','Revenues','us-gaap/2024','','20240930','1','USD','not_a_number','','','2024q3')""")
    create_fact_table(con)
    assert build_facts(con) == 2
    out = capsys.readouterr().out
    assert "2 of 4 rows skipped" in out
    assert "1 malformed" in out
    assert "1 filed before" in out


def test_competing_tags_in_one_filing_yield_one_fact(tmp_path):
    """A filing reporting both NetIncomeLoss and ProfitLoss for the same
    period has reported one net income, twice, under two tags — not two
    facts. MR-0010 outranks MR-0011 ("used when NetIncomeLoss absent"), so
    NetIncomeLoss wins and ProfitLoss emits nothing.

    Modelled on cik=1452936, which filed NetIncomeLoss -144,151,000
    alongside three different ProfitLoss values in a single accession.
    """
    con = connect(tmp_path / "t.duckdb")
    init_schema(con); create_mapping_table(con); seed_mapping_rules(con)
    con.execute("""INSERT INTO raw_sub VALUES
        ('a1','1452936','ACME','3571','1231','10-K','20231231','2023','FY','20240315','0','1','1','2024q1')""")
    con.execute("""INSERT INTO raw_num VALUES
        ('a1','NetIncomeLoss','us-gaap/2023','','20231231','4','USD','-144151000','','','2024q1'),
        ('a1','ProfitLoss','us-gaap/2023','','20231231','4','USD','-140000000','','','2024q1')""")
    create_fact_table(con)
    n = build_facts(con)
    assert n == 1
    rows = con.execute(
        "SELECT source_tag, value, mapping_rule_id FROM fact "
        "WHERE canonical_field = 'net_income'").fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "NetIncomeLoss"
    assert rows[0][1] == -144151000.0
    assert rows[0][2] == "MR-0010"


def test_lower_priority_tag_used_when_winner_absent(tmp_path):
    """Arbitration must not delete the fallback: a filing that reports only
    ProfitLoss still yields a net_income fact."""
    con = connect(tmp_path / "t.duckdb")
    init_schema(con); create_mapping_table(con); seed_mapping_rules(con)
    con.execute("""INSERT INTO raw_sub VALUES
        ('a1','1452936','ACME','3571','1231','10-K','20231231','2023','FY','20240315','0','1','1','2024q1')""")
    con.execute("""INSERT INTO raw_num VALUES
        ('a1','ProfitLoss','us-gaap/2023','','20231231','4','USD','-140000000','','','2024q1')""")
    create_fact_table(con)
    assert build_facts(con) == 1
    row = con.execute(
        "SELECT source_tag, value FROM fact "
        "WHERE canonical_field = 'net_income'").fetchone()
    assert row == ("ProfitLoss", -140000000.0)


def test_three_competing_revenue_tags_collapse_to_the_highest_priority(tmp_path):
    """revenue has four competing tags. A filing using three of them has
    reported revenue once; the ASC 606 element (MR-0001) is preferred."""
    con = connect(tmp_path / "t.duckdb")
    init_schema(con); create_mapping_table(con); seed_mapping_rules(con)
    con.execute("""INSERT INTO raw_sub VALUES
        ('a1','320193','APPLE','3571','0930','10-Q','20240630','2024','Q3','20240802','0','1','1','2024q3')""")
    con.execute("""INSERT INTO raw_num VALUES
        ('a1','SalesRevenueNet','us-gaap/2024','','20240630','1','USD','85000000000','','','2024q3'),
        ('a1','Revenues','us-gaap/2024','','20240630','1','USD','85500000000','','','2024q3'),
        ('a1','RevenueFromContractWithCustomerExcludingAssessedTax','us-gaap/2024','','20240630','1','USD','85777000000','','','2024q3')""")
    create_fact_table(con)
    assert build_facts(con) == 1
    rows = con.execute(
        "SELECT source_tag, value FROM fact "
        "WHERE canonical_field = 'revenue'").fetchall()
    assert rows == [
        ("RevenueFromContractWithCustomerExcludingAssessedTax", 85777000000.0)]


def test_arbitration_is_scoped_to_one_filing_and_period(tmp_path):
    """Arbitration resolves competition *inside* one filing-period-unit. It
    must not reach across filings (that is a restatement) or across periods
    (those are different figures)."""
    con = connect(tmp_path / "t.duckdb")
    init_schema(con); create_mapping_table(con); seed_mapping_rules(con)
    con.execute("""INSERT INTO raw_sub VALUES
        ('a1','1','CO','3571','1231','10-K','20231231','2023','FY','20240315','0','1','1','2024q1'),
        ('a2','1','CO','3571','1231','10-K/A','20231231','2023','FY','20240601','0','1','1','2024q2')""")
    con.execute("""INSERT INTO raw_num VALUES
        ('a1','NetIncomeLoss','us-gaap/2023','','20231231','4','USD','-144151000','','','2024q1'),
        ('a1','ProfitLoss','us-gaap/2023','','20231231','4','USD','-140000000','','','2024q1'),
        ('a1','NetIncomeLoss','us-gaap/2023','','20231231','1','USD','-30000000','','','2024q1'),
        ('a2','ProfitLoss','us-gaap/2023','','20231231','4','USD','-145000000','','','2024q2')""")
    create_fact_table(con)
    n = build_facts(con)
    # a1 annual (arbitrated to NetIncomeLoss), a1 quarterly, a2 restatement
    assert n == 3
    rows = con.execute(
        "SELECT accession, period_start, source_tag, value FROM fact "
        "WHERE canonical_field = 'net_income' "
        "ORDER BY accession, period_start").fetchall()
    assert [(r[0], r[2], r[3]) for r in rows] == [
        ("a1", "NetIncomeLoss", -144151000.0),
        ("a1", "NetIncomeLoss", -30000000.0),
        ("a2", "ProfitLoss", -145000000.0),
    ]


def test_arbitration_is_per_unit(tmp_path):
    """The same figure in two currencies is two figures, and each is
    arbitrated on its own — collapsing them would silently drop one."""
    con = connect(tmp_path / "t.duckdb")
    init_schema(con); create_mapping_table(con); seed_mapping_rules(con)
    con.execute("""INSERT INTO raw_sub VALUES
        ('a1','1296774','CO','3571','1231','10-K','20231231','2023','FY','20240315','0','1','1','2024q1')""")
    con.execute("""INSERT INTO raw_num VALUES
        ('a1','Assets','us-gaap/2023','','20231231','0','USD','205617546','','','2024q1'),
        ('a1','Assets','us-gaap/2023','','20231231','0','CNY','1310318375','','','2024q1')""")
    create_fact_table(con)
    assert build_facts(con) == 2
    rows = con.execute(
        "SELECT unit, value FROM fact WHERE canonical_field = 'total_assets' "
        "ORDER BY unit").fetchall()
    assert rows == [("CNY", 1310318375.0), ("USD", 205617546.0)]
