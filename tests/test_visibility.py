from datetime import date

from edgar.db import connect
from edgar.curate.facts import create_fact_table
from edgar.narrative.store import create_narrative_tables
from edgar.tools.compute import compute, create_derivation_table
from edgar.tools.visibility import check_integrity, visible_asof


def _fact(con, *, fact_id, cik=1, field="revenue", value=100.0, unit="USD",
          ptype="duration", pstart=date(2023, 1, 1), pend=date(2023, 3, 31),
          filed=date(2023, 5, 1), accn="a-1", tag="Revenues", rule="MR-0003"):
    con.execute(
        "INSERT INTO fact VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [fact_id, cik, field, value, unit, ptype, pstart, pend,
         "2023", "Q1", filed, accn, tag, rule, 1.0, "2023q2"],
    )


def _con(tmp_path):
    con = connect(tmp_path / "t.duckdb")
    create_fact_table(con)
    create_derivation_table(con)
    create_narrative_tables(con)
    return con


def test_visible_asof_fact_span_and_unknown(tmp_path):
    con = _con(tmp_path)
    _fact(con, fact_id="fA", filed=date(2023, 5, 1))
    con.execute("INSERT INTO span VALUES ('sB','d1',1,'a-1','10-K',"
               "'Item 7',DATE '2023-05-01',0,40,'text',NULL)")
    assert visible_asof(con, "fA", date(2023, 6, 1)) is None
    assert "after as_of" in visible_asof(con, "fA", date(2023, 1, 1))
    assert visible_asof(con, "sB", date(2023, 6, 1)) is None
    assert "unknown identifier" in visible_asof(con, "nope", date(2023, 6, 1))


def test_visible_asof_derivation_is_date_only_no_recompute(tmp_path):
    con = _con(tmp_path)
    _fact(con, fact_id="gp", field="gross_profit", value=40.0)
    _fact(con, fact_id="rev", field="revenue", value=100.0)
    d = compute(con, "gp / rev", {"gp": "gp", "rev": "rev"},
               date(2023, 6, 1))
    assert visible_asof(con, d.derivation_id, date(2023, 6, 1)) is None
    assert "unknown derivation" in visible_asof(con, "D-nope",
                                                date(2023, 6, 1))
    # tamper the stored value: visible_asof must NOT notice (date only)
    con.execute("UPDATE derivation SET value = 0.9 WHERE derivation_id = ?",
               [d.derivation_id])
    assert visible_asof(con, d.derivation_id, date(2023, 6, 1)) is None


def test_check_integrity_derivation_only(tmp_path):
    con = _con(tmp_path)
    _fact(con, fact_id="gp", field="gross_profit", value=40.0)
    _fact(con, fact_id="rev", field="revenue", value=100.0)
    d = compute(con, "gp / rev", {"gp": "gp", "rev": "rev"},
               date(2023, 6, 1))
    assert check_integrity(con, d.derivation_id) is None
    assert check_integrity(con, "fA") is None       # not a derivation id
    assert check_integrity(con, "D-nope") is None   # nothing to recompute
    con.execute("UPDATE derivation SET value = 0.9 WHERE derivation_id = ?",
               [d.derivation_id])
    problem = check_integrity(con, d.derivation_id)
    assert problem is not None and "recomputes" in problem
