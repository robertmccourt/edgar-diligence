from edgar.db import connect, init_schema
from edgar.curate.mapping import create_mapping_table, seed_mapping_rules, CANONICAL_FIELDS
from edgar.curate.facts import create_fact_table, build_facts
from edgar.quality.coverage_stats import mapping_coverage


def _base(con):
    init_schema(con)
    create_mapping_table(con)
    seed_mapping_rules(con)
    create_fact_table(con)
    con.execute("""INSERT INTO raw_sub VALUES
        ('a1','1','CO','3571','1231','10-K','20231231','2023','FY','20240315','0','1','1','2024q1')""")


def test_funnel_reconciles_arithmetically(tmp_path):
    con = connect(tmp_path / "t.duckdb")
    _base(con)
    con.executemany(
        "INSERT INTO raw_num VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        [
            # eligible: mapped, no segments, no coreg, has value
            ("a1", "Revenues", "us-gaap/2023", "", "20231231", "4", "USD",
             "100", "", "", "2024q1"),
            # dropped_segments: mapped, non-empty segments
            ("a1", "Revenues", "us-gaap/2023", "", "20231231", "4", "USD",
             "200", "", "BusinessSegments=Widgets;", "2024q1"),
            # dropped_coreg: mapped, empty segments, non-empty coreg
            ("a1", "Revenues", "us-gaap/2023", "X", "20231231", "4", "USD",
             "300", "", "", "2024q1"),
            # dropped_null_value: mapped, empty segments, empty coreg, blank value
            ("a1", "Revenues", "us-gaap/2023", "", "20231231", "1", "USD",
             "", "", "", "2024q1"),
            # unmapped: tag has no mapping rule
            ("a1", "SomeCustomTag", "acme/2023", "", "20231231", "4", "USD",
             "999", "", "", "2024q1"),
        ],
    )
    result = mapping_coverage(con)
    assert result["raw_rows"] == 5
    assert result["unmapped_tag_rows"] == 1
    assert result["mapped_tag_rows"] == 4
    assert result["unmapped_tag_rows"] + result["mapped_tag_rows"] == result["raw_rows"]
    assert result["dropped_segments"] == 1
    assert result["dropped_coreg"] == 1
    assert result["dropped_null_value"] == 1
    assert result["eligible_rows"] == 1
    assert (result["mapped_tag_rows"] - result["dropped_segments"]
            - result["dropped_coreg"] - result["dropped_null_value"]
            == result["eligible_rows"])


def test_segment_row_counted_as_dropped_and_excluded_from_eligible(tmp_path):
    con = connect(tmp_path / "t.duckdb")
    _base(con)
    con.execute("""INSERT INTO raw_num VALUES
        ('a1','Revenues','us-gaap/2023','','20231231','4','USD','100','',
         'BusinessSegments=Widgets;','2024q1')""")
    result = mapping_coverage(con)
    assert result["dropped_segments"] == 1
    assert result["eligible_rows"] == 0


def test_null_segments_not_treated_as_dropped(tmp_path):
    con = connect(tmp_path / "t.duckdb")
    _base(con)
    con.execute("""INSERT INTO raw_num VALUES
        ('a1','Revenues','us-gaap/2023','','20231231','4','USD','100','',
         NULL,'2024q1')""")
    result = mapping_coverage(con)
    assert result["dropped_segments"] == 0
    assert result["eligible_rows"] == 1


def test_field_coverage_has_entry_for_every_canonical_field_even_zero(tmp_path):
    con = connect(tmp_path / "t.duckdb")
    _base(con)
    con.execute("""INSERT INTO raw_num VALUES
        ('a1','Revenues','us-gaap/2023','','20231231','4','USD','100','','','2024q1')""")
    build_facts(con)
    result = mapping_coverage(con)
    assert set(result["field_coverage"].keys()) == set(CANONICAL_FIELDS)
    assert result["field_coverage"]["revenue"]["facts"] == 1
    assert result["field_coverage"]["revenue"]["companies"] == 1
    assert result["field_coverage"]["capex"] == {"facts": 0, "companies": 0}


def test_top_unmapped_tags_ordered_and_capped(tmp_path):
    con = connect(tmp_path / "t.duckdb")
    _base(con)
    rows = []
    # 25 distinct unmapped tags, with descending row counts so ordering is checkable
    for i in range(25):
        tag = f"UnmappedTag{i:02d}"
        count = 25 - i
        for _ in range(count):
            rows.append(
                ("a1", tag, "acme/2023", "", "20231231", "4", "USD",
                 "1", "", "", "2024q1")
            )
    con.executemany(
        "INSERT INTO raw_num VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows
    )
    result = mapping_coverage(con)
    top = result["top_unmapped_tags"]
    assert len(top) == 20
    row_counts = [t["rows"] for t in top]
    assert row_counts == sorted(row_counts, reverse=True)
    assert top[0] == {"tag": "UnmappedTag00", "rows": 25}
