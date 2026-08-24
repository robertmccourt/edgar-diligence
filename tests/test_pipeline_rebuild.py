from datetime import date

import pytest

import edgar.pipeline
from edgar.db import connect, init_schema
from edgar.pipeline import rebuild_curated

def _seed_raw(con):
    init_schema(con)
    con.execute("INSERT INTO raw_sub VALUES ('a-1','1','ACME','3571','1231',"
                "'10-Q','20230331','2023','Q1','20230501','0','1','1','2023q2')")
    # An instant fact under a NEW tag: proves rebuild picks up new rules.
    con.execute("INSERT INTO raw_num VALUES ('a-1','InventoryNet','us-gaap/2023',"
                "NULL,'20230331','0','USD','500',NULL,NULL,'2023q2')")

def test_rebuild_creates_facts_for_new_fields(tmp_path):
    con = connect(tmp_path / "t.duckdb")
    _seed_raw(con)
    report = rebuild_curated(con)
    row = con.execute("SELECT canonical_field, value, period_type, period_start "
                      "FROM fact").fetchone()
    assert row[0] == "inventory" and row[1] == 500.0
    assert row[2] == "instant" and row[3] is None
    assert report["facts"] >= 1 and "eligibility" in report

def test_rebuild_is_idempotent(tmp_path):
    con = connect(tmp_path / "t.duckdb")
    _seed_raw(con)
    rebuild_curated(con)
    n1 = con.execute("SELECT count(*) FROM fact").fetchone()[0]
    rebuild_curated(con)
    assert con.execute("SELECT count(*) FROM fact").fetchone()[0] == n1


def test_rebuild_interrupted_during_staging_leaves_facts_intact(
        tmp_path, monkeypatch):
    """Root cause of the 2026-08-21 pilot finding: two successive real-store
    rebuilds were killed mid-insert by machine sleep, silently leaving an
    arbitrary prefix (~2.6M of ~4.7M facts). Inserts now target a staging
    table, so a kill during the (long) load phase never touches `fact`."""
    con = connect(tmp_path / "t.duckdb")
    _seed_raw(con)
    rebuild_curated(con)
    before = con.execute(
        "SELECT fact_id FROM fact ORDER BY fact_id").fetchall()
    assert before  # sanity: there is a prior good state to protect

    def _dies_mid_staging(c, table="fact", chunk_size=0):
        c.execute(f"INSERT INTO {table} SELECT * FROM fact LIMIT 1")
        raise KeyboardInterrupt("machine slept")

    monkeypatch.setattr(edgar.pipeline, "build_facts", _dies_mid_staging)
    with pytest.raises(KeyboardInterrupt):
        rebuild_curated(con)
    after = con.execute(
        "SELECT fact_id FROM fact ORDER BY fact_id").fetchall()
    assert after == before


def test_rebuild_interrupted_during_swap_rolls_back(tmp_path, monkeypatch):
    """The only mutation of `fact` is the drop+rename swap transaction; an
    interruption inside it must roll back to the previous table."""
    con = connect(tmp_path / "t.duckdb")
    _seed_raw(con)
    rebuild_curated(con)
    before = con.execute(
        "SELECT fact_id FROM fact ORDER BY fact_id").fetchall()

    def _dies_inside_swap(c):  # runs after DROP fact + RENAME staging
        raise KeyboardInterrupt("machine slept")

    monkeypatch.setattr(edgar.pipeline, "build_company_table",
                        _dies_inside_swap)
    with pytest.raises(KeyboardInterrupt):
        rebuild_curated(con)
    after = con.execute(
        "SELECT fact_id FROM fact ORDER BY fact_id").fetchall()
    assert after == before


def test_rebuild_verifies_persisted_count_matches_table(tmp_path):
    con = connect(tmp_path / "t.duckdb")
    _seed_raw(con)
    report = rebuild_curated(con)
    assert report["facts"] == con.execute(
        "SELECT count(*) FROM fact").fetchone()[0]
