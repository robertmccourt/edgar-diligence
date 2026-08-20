from edgar.db import connect
from edgar.curate.mapping import (
    CANONICAL_FIELDS, SEED_RULES, rules_for_tag,
    create_mapping_table, seed_mapping_rules,
)

def test_exactly_fifteen_canonical_fields():
    assert len(CANONICAL_FIELDS) == 15
    assert CANONICAL_FIELDS[:10] == (
        "revenue", "cost_of_revenue", "gross_profit", "operating_income",
        "net_income", "total_assets", "total_liabilities",
        "stockholders_equity", "operating_cash_flow", "capex",
    )
    assert CANONICAL_FIELDS[10:] == (
        "inventory", "accounts_receivable", "accounts_payable",
        "long_term_debt", "cash_and_equivalents",
    )

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

def test_new_field_priorities():
    assert rules_for_tag("InventoryNet")[0].canonical_field == "inventory"
    assert rules_for_tag("AccountsReceivableNetCurrent")[0].canonical_field == "accounts_receivable"
    assert rules_for_tag("AccountsPayableCurrent")[0].canonical_field == "accounts_payable"
    assert rules_for_tag("CashAndCashEquivalentsAtCarryingValue")[0].canonical_field == "cash_and_equivalents"
    ltd = sorted((r for r in SEED_RULES if r.canonical_field == "long_term_debt"),
                 key=lambda r: r.priority)
    assert [r.source_tag for r in ltd] == [
        "LongTermDebt", "LongTermDebtAndCapitalLeaseObligations",
        "LongTermDebtNoncurrent"]

def test_accrued_liabilities_combo_tag_is_not_mapped():
    assert rules_for_tag("AccountsPayableAndAccruedLiabilitiesCurrent") == []

def test_thirty_rules_total():
    assert len(SEED_RULES) == 30

def test_seed_writes_rules_to_db(tmp_path):
    con = connect(tmp_path / "t.duckdb")
    create_mapping_table(con)
    n = seed_mapping_rules(con)
    assert n == len(SEED_RULES)
    seed_mapping_rules(con)  # idempotent
    assert con.execute("SELECT count(*) FROM mapping_rule").fetchone()[0] == n
