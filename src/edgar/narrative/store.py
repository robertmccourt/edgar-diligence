import duckdb

NARRATIVE_DDL = """
CREATE TABLE IF NOT EXISTS narrative_doc (
    doc_id VARCHAR PRIMARY KEY,
    cik BIGINT, accession VARCHAR, form VARCHAR, filed_date DATE,
    fiscal_year VARCHAR, item VARCHAR, text VARCHAR
);
CREATE TABLE IF NOT EXISTS span (
    span_id VARCHAR PRIMARY KEY,
    doc_id VARCHAR, cik BIGINT, accession VARCHAR, form VARCHAR,
    item VARCHAR, filed_date DATE,
    char_start INTEGER, char_end INTEGER, text VARCHAR,
    embedding FLOAT[384]
);
"""


def create_narrative_tables(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(NARRATIVE_DDL)
