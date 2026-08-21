from datetime import date

from edgar.db import connect
from edgar.curate.facts import create_fact_table
from edgar.tools.peers import get_peer_set


def _fact(con, *, fact_id, cik=1, field="revenue", value=100.0, unit="USD",
          ptype="duration", pstart=date(2023, 1, 1), pend=date(2023, 3, 31),
          filed=date(2023, 5, 1), accn="a-1", tag="Revenues", rule="MR-0003"):
    con.execute(
        "INSERT INTO fact VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [fact_id, cik, field, value, unit, ptype, pstart, pend,
         "2023", "Q1", filed, accn, tag, rule, 1.0, "2023q2"],
    )


def _company(con, cik, sic, sector="manufacturing", status="eligible"):
    con.execute(
        "INSERT INTO company VALUES (?,?,?,?,?,?,?,?)",
        [cik, f"CO{cik}", sic, sector, 12, date(2019, 1, 1), status, None])


def _db(tmp_path):
    con = connect(tmp_path / "t.duckdb")
    create_fact_table(con)
    con.execute("""CREATE TABLE company (cik BIGINT, name VARCHAR,
        sic VARCHAR, sector VARCHAR, fiscal_year_end_month INTEGER,
        first_filing_date DATE, eligibility_status VARCHAR,
        exclusion_reason VARCHAR)""")
    return con


def test_same_two_digit_sic_and_visibility(tmp_path):
    con = _db(tmp_path)
    _company(con, 1, "3571"); _company(con, 2, "3572")
    _company(con, 3, "2911")
    _fact(con, fact_id="a", cik=2, filed=date(2023, 5, 1))
    _fact(con, fact_id="b", cik=3, filed=date(2023, 5, 1))
    ps = get_peer_set(con, 1, as_of=date(2023, 6, 1), min_peers=1)
    assert [p.cik for p in ps.peers] == [2]
    assert "35" in ps.selection_rule


def test_ineligible_and_unfiled_peers_excluded(tmp_path):
    con = _db(tmp_path)
    _company(con, 1, "3571")
    _company(con, 2, "3572", status="excluded")
    _company(con, 4, "3579")
    _fact(con, fact_id="a", cik=2, filed=date(2023, 5, 1))
    _fact(con, fact_id="c", cik=4, filed=date(2024, 5, 1))
    ps = get_peer_set(con, 1, as_of=date(2023, 6, 1), min_peers=1)
    assert ps.peers == []


def test_widens_to_sector_when_sic_too_thin(tmp_path):
    con = _db(tmp_path)
    _company(con, 1, "3571"); _company(con, 5, "2911")
    _fact(con, fact_id="e", cik=5, filed=date(2023, 5, 1))
    ps = get_peer_set(con, 1, as_of=date(2023, 6, 1), min_peers=1)
    assert [p.cik for p in ps.peers] == [5]
    assert "sector" in ps.selection_rule
