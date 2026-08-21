from datetime import date

import pytest

from edgar.db import connect, init_schema
from edgar.curate.facts import create_fact_table
from edgar.curate.mapping import create_mapping_table
from edgar.tools.facts_tools import (
    get_fact_history, get_facts, list_available_facts)


def _db(tmp_path):
    con = connect(tmp_path / "t.duckdb")
    init_schema(con)
    create_fact_table(con)
    create_mapping_table(con)
    return con


def _fact(con, *, fact_id, cik=1, field="revenue", value=100.0, unit="USD",
          ptype="duration", pstart=date(2023, 1, 1), pend=date(2023, 3, 31),
          filed=date(2023, 5, 1), accn="a-1", tag="Revenues", rule="MR-0003"):
    con.execute(
        "INSERT INTO fact VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [fact_id, cik, field, value, unit, ptype, pstart, pend,
         "2023", "Q1", filed, accn, tag, rule, 1.0, "2023q2"],
    )


def test_get_facts_returns_visible_fact_and_flags_missing(tmp_path):
    con = _db(tmp_path)
    _fact(con, fact_id="f1", field="revenue", filed=date(2023, 5, 1))
    r = get_facts(con, 1, ["revenue", "inventory"],
                  date(2023, 1, 1), date(2023, 3, 31), as_of=date(2023, 6, 1))
    assert [f.fact_id for f in r.facts] == ["f1"]
    missing = {m.canonical_field: m.status for m in r.missing}
    assert missing == {"inventory": "NOT_DISCLOSED"}


def test_get_facts_hides_later_filings_and_reports_not_yet_filed(tmp_path):
    con = _db(tmp_path)
    _fact(con, fact_id="f1", field="revenue", filed=date(2023, 5, 1))
    r = get_facts(con, 1, ["revenue"], date(2023, 1, 1), date(2023, 3, 31),
                  as_of=date(2023, 4, 1))   # before the filing
    assert r.facts == []
    assert r.missing[0].status == "NOT_YET_FILED"


def test_list_available_facts_orders_new_to_old_and_respects_as_of(tmp_path):
    con = _db(tmp_path)
    _fact(con, fact_id="q1", pend=date(2023, 3, 31), filed=date(2023, 5, 1))
    _fact(con, fact_id="q2", pend=date(2023, 6, 30), filed=date(2023, 8, 1))
    rep = list_available_facts(con, 1, as_of=date(2023, 6, 1))
    assert [e.period_end for e in rep.entries] == [date(2023, 3, 31)]
    assert rep.entries[0].statuses["revenue"] == "AVAILABLE"
    assert rep.entries[0].statuses["inventory"] == "NOT_DISCLOSED"


def test_get_facts_rejects_unknown_field(tmp_path):
    con = _db(tmp_path)
    with pytest.raises(ValueError, match="unknown canonical field"):
        get_facts(con, 1, ["ebitda"], date(2023, 1, 1), date(2023, 3, 31),
                  as_of=date(2023, 6, 1))


def test_get_fact_history_caps_to_as_of(tmp_path):
    """I3: restatement_history returns every version regardless of when
    it was filed; get_fact_history is the tool surface and MUST apply the
    point-in-time cap itself."""
    con = _db(tmp_path)
    _fact(con, fact_id="fOld", value=90.0, filed=date(2023, 5, 1),
          accn="a-1")
    _fact(con, fact_id="fNew", value=95.0, filed=date(2023, 8, 1),
          accn="a-2")
    hist = get_fact_history(con, 1, "revenue", date(2023, 3, 31),
                            date(2023, 6, 1),
                            period_start=date(2023, 1, 1),
                            period_type="duration", unit="USD")
    assert [f.fact_id for f in hist] == ["fOld"]

    hist_full = get_fact_history(con, 1, "revenue", date(2023, 3, 31),
                                 date(2023, 12, 31),
                                 period_start=date(2023, 1, 1),
                                 period_type="duration", unit="USD")
    assert [f.fact_id for f in hist_full] == ["fOld", "fNew"]
