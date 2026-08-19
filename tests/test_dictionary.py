from edgar.db import connect
from edgar.curate.facts import create_fact_table
from edgar.curate.mapping import create_mapping_table, seed_mapping_rules
from edgar.quality.dictionary import generate_data_dictionary, FIELD_DEFINITIONS
from edgar.curate.mapping import CANONICAL_FIELDS

def test_every_canonical_field_has_a_definition():
    for f in CANONICAL_FIELDS:
        assert f in FIELD_DEFINITIONS
        assert len(FIELD_DEFINITIONS[f]) > 20

def test_dictionary_lists_tables_and_fields(tmp_path):
    con = connect(tmp_path / "t.duckdb")
    create_fact_table(con); create_mapping_table(con); seed_mapping_rules(con)
    md = generate_data_dictionary(con)
    assert "## fact" in md
    assert "filed_date" in md
    assert "revenue" in md
    assert "RevenueFromContractWithCustomerExcludingAssessedTax" in md
