from datetime import date

from edgar.db import connect
from edgar.curate.facts import create_fact_table
from edgar.narrative.store import create_narrative_tables
from edgar.memory.episodic import create_memory_tables, save_session
from edgar.tools.compute import compute, create_derivation_table
from edgar.agent.llm import FakeLLM
from edgar.eval.judges import judge_claim, temporal_leakage
from edgar.eval.schemas import JudgeOpinion, RawClaim


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
    create_memory_tables(con)
    return con


def test_numeric_supported_scale_aware(tmp_path):
    con = _con(tmp_path)
    _fact(con, fact_id="fA", value=2_110_000_000.0, filed=date(2023, 5, 1))
    c = RawClaim(claim_text="Revenue was $2.1B", claim_type="NUMERIC",
                 citations=["fA"], claimed_value=2.1)
    v = judge_claim(con, FakeLLM(), c, as_of=date(2023, 6, 1))
    assert v.status == "SUPPORTED"


def test_numeric_contradicted_when_value_off(tmp_path):
    con = _con(tmp_path)
    _fact(con, fact_id="fA", value=2_110_000_000.0, filed=date(2023, 5, 1))
    c = RawClaim(claim_text="Revenue was $3.4B", claim_type="NUMERIC",
                 citations=["fA"], claimed_value=3.4)
    assert judge_claim(con, FakeLLM(), c,
                       as_of=date(2023, 6, 1)).status == "CONTRADICTED"


def test_derived_recomputes_from_cited_derivation(tmp_path):
    con = _con(tmp_path)
    _fact(con, fact_id="gp", field="gross_profit", value=40.0)
    _fact(con, fact_id="rev", field="revenue", value=100.0)
    d = compute(con, "gp / rev", {"gp": "gp", "rev": "rev"},
                date(2023, 6, 1))
    good = RawClaim(claim_text="Gross margin was 40%", claim_type="DERIVED",
                    citations=[d.derivation_id], claimed_value=0.40)
    assert judge_claim(con, FakeLLM(), good,
                       as_of=date(2023, 6, 1)).status == "SUPPORTED"


def test_attributed_uses_llm_over_span_text(tmp_path):
    con = _con(tmp_path)
    con.execute("INSERT INTO span VALUES ('sB','d1',1,'a-1','10-K',"
                "'Item 7',DATE '2023-05-01',0,40,"
                "'Freight costs pressured margins.',NULL)")
    llm = FakeLLM(parsed=[JudgeOpinion(status="SUPPORTED",
                                       reason="text says exactly this")])
    c = RawClaim(claim_text="Management cited freight costs",
                 claim_type="ATTRIBUTED", citations=["sB"])
    v = judge_claim(con, llm, c, as_of=date(2023, 6, 1))
    assert v.status == "SUPPORTED"
    assert "Freight costs pressured" in llm.parse_calls[0]["prompt"]


def test_unsupported_short_circuits_without_llm(tmp_path):
    con = _con(tmp_path)
    c = RawClaim(claim_text="EBITDA doubled", claim_type="UNSUPPORTED")
    v = judge_claim(con, FakeLLM(), c, as_of=date(2023, 6, 1))
    assert v.status == "UNSUPPORTED"


def test_temporal_leakage_both_surfaces(tmp_path):
    con = _con(tmp_path)
    _fact(con, fact_id="fLate", filed=date(2024, 5, 1))
    save_session(con, session_id="S1", cik=1, as_of=date(2023, 6, 1),
                 config_version="v1", trace_id="t", question=None,
                 recalled_conclusion_ids=["C-x"])
    con.execute("INSERT INTO session_conclusion VALUES "
                "('C-x','s0',1,'future conclusion',DATE '2025-01-01','t')")
    claims = [RawClaim(claim_text="x", claim_type="NUMERIC",
                       citations=["fLate"], claimed_value=1.0)]
    problems = temporal_leakage(con, claims, as_of=date(2023, 6, 1),
                                session_id="S1")
    assert len(problems) == 2
    assert any("fLate" in p for p in problems)
    assert any("C-x" in p for p in problems)
