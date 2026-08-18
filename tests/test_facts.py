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
