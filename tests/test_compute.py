from datetime import date

import pytest

from edgar.db import connect
from edgar.curate.facts import create_fact_table
from edgar.tools.compute import (
    ComputeError, compute, create_derivation_table, recompute)


def _db(tmp_path):
    con = connect(tmp_path / "t.duckdb")
    create_fact_table(con)
    return con


def _fact(con, *, fact_id, cik=1, field="revenue", value=100.0, unit="USD",
          ptype="duration", pstart=date(2023, 1, 1), pend=date(2023, 3, 31),
          filed=date(2023, 5, 1), accn="a-1", tag="Revenues", rule="MR-0003"):
    con.execute(
        "INSERT INTO fact VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [fact_id, cik, field, value, unit, ptype, pstart, pend,
         "2023", "Q1", filed, accn, tag, rule, 1.0, "2023q2"],
    )


def _setup(tmp_path):
    con = _db(tmp_path)
    create_derivation_table(con)
    _fact(con, fact_id="gp", field="gross_profit", value=40.0)
    _fact(con, fact_id="rev", field="revenue", value=100.0)
    _fact(con, fact_id="inv", field="inventory", value=30.0,
          ptype="instant", pstart=None)
    return con


def test_margin_with_derivation_record(tmp_path):
    con = _setup(tmp_path)
    c = compute(con, "gp / rev", {"gp": "gp", "rev": "rev"}, date(2023, 6, 1))
    assert c.value == pytest.approx(0.4)
    assert c.values == {"gp": 40.0, "rev": 100.0}
    assert c.derivation_id.startswith("D-")
    again = recompute(con, c.derivation_id)
    assert again.value == pytest.approx(0.4)


def test_rejects_additive_type_mixing_but_allows_ratio(tmp_path):
    con = _setup(tmp_path)
    with pytest.raises(ComputeError, match="period_type"):
        compute(con, "rev + inv", {"rev": "rev", "inv": "inv"},
                date(2023, 6, 1))
    days = compute(con, "inv / rev * 91", {"inv": "inv", "rev": "rev"},
                   date(2023, 6, 1))
    assert days.value == pytest.approx(27.3)


def test_rejects_input_filed_after_as_of(tmp_path):
    con = _setup(tmp_path)
    with pytest.raises(ComputeError, match="filed after as_of"):
        compute(con, "rev * 1", {"rev": "rev"}, date(2023, 4, 1))


def test_rejects_unsafe_and_unknown(tmp_path):
    con = _setup(tmp_path)
    for expr, inputs in [
        ("__import__('os')", {}),
        ("rev.value", {"rev": "rev"}),
        ("rev + missing", {"rev": "rev"}),          # name not in inputs
        ("rev", {"rev": "rev", "gp": "gp"}),        # unused input
    ]:
        with pytest.raises(ComputeError):
            compute(con, expr, inputs, date(2023, 6, 1))
    with pytest.raises(ComputeError, match="no such fact"):
        compute(con, "x * 1", {"x": "nope"}, date(2023, 6, 1))


def test_cross_company_calendar_guard(tmp_path):
    con = _setup(tmp_path)
    _fact(con, fact_id="peer_rev", cik=2, field="revenue", value=50.0,
          pstart=date(2023, 4, 1), pend=date(2023, 6, 30))
    with pytest.raises(ComputeError, match="calendar"):
        compute(con, "rev / peer", {"rev": "rev", "peer": "peer_rev"},
                date(2023, 9, 1))


def test_division_by_zero_is_compute_error(tmp_path):
    con = _setup(tmp_path)
    _fact(con, fact_id="z", field="capex", value=0.0)
    with pytest.raises(ComputeError, match="division by zero"):
        compute(con, "rev / z", {"rev": "rev", "z": "z"}, date(2023, 6, 1))
