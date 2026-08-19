from edgar.db import connect
from edgar.curate.mapping import (
    CANONICAL_FIELDS, SEED_RULES, rules_for_tag,
    create_mapping_table, seed_mapping_rules,
)

def test_exactly_ten_canonical_fields():
    assert len(CANONICAL_FIELDS) == 10
    assert "revenue" in CANONICAL_FIELDS
    assert "capex" in CANONICAL_FIELDS

def test_every_rule_targets_a_canonical_field():
    for r in SEED_RULES:
        assert r.canonical_field in CANONICAL_FIELDS

def test_mapping_rule_ids_are_unique():
    ids = [r.mapping_rule_id for r in SEED_RULES]
    assert len(ids) == len(set(ids))

def test_revenue_has_multiple_candidate_tags():
    tags = {r.source_tag for r in SEED_RULES if r.canonical_field == "revenue"}
    assert "Revenues" in tags
    assert "RevenueFromContractWithCustomerExcludingAssessedTax" in tags

def test_priority_disambiguates_revenue_tags():
    rs = sorted(
        (r for r in SEED_RULES if r.canonical_field == "revenue"),
        key=lambda r: r.priority,
    )
    assert rs[0].source_tag == "RevenueFromContractWithCustomerExcludingAssessedTax"

def test_rules_for_tag_returns_matches():
    assert rules_for_tag("Assets")[0].canonical_field == "total_assets"
    assert rules_for_tag("NoSuchTag") == []

def test_seed_writes_rules_to_db(tmp_path):
    con = connect(tmp_path / "t.duckdb")
    create_mapping_table(con)
    n = seed_mapping_rules(con)
    assert n == len(SEED_RULES)
    seed_mapping_rules(con)  # idempotent
    assert con.execute("SELECT count(*) FROM mapping_rule").fetchone()[0] == n
