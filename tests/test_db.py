from edgar.db import connect, init_schema

def test_init_schema_creates_raw_tables(tmp_path):
    con = connect(tmp_path / "t.duckdb")
    init_schema(con)
    names = {r[0] for r in con.execute(
        "SELECT table_name FROM information_schema.tables"
    ).fetchall()}
    assert {"raw_sub", "raw_num", "raw_tag", "raw_pre"} <= names

def test_init_schema_is_idempotent(tmp_path):
    con = connect(tmp_path / "t.duckdb")
    init_schema(con)
    init_schema(con)
    assert con.execute("SELECT count(*) FROM raw_sub").fetchone()[0] == 0
