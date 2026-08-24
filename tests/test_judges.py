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


def test_numeric_supported_at_bounded_tolerance(tmp_path):
    """$2.1B rounds off $2.11B — within the 2% band once extraction has
    normalised "billion" into the value (spec 8.0)."""
    con = _con(tmp_path)
    _fact(con, fact_id="fA", value=2_110_000_000.0, filed=date(2023, 5, 1))
    c = RawClaim(claim_text="Revenue was $2.1B", claim_type="NUMERIC",
                 citations=["fA"], claimed_value=2.1e9)
    v = judge_claim(con, FakeLLM(), c, as_of=date(2023, 6, 1))
    assert v.status == "SUPPORTED"


def test_numeric_contradicted_when_value_off(tmp_path):
    con = _con(tmp_path)
    _fact(con, fact_id="fA", value=2_110_000_000.0, filed=date(2023, 5, 1))
    c = RawClaim(claim_text="Revenue was $3.4B", claim_type="NUMERIC",
                 citations=["fA"], claimed_value=3.4e9)
    assert judge_claim(con, FakeLLM(), c,
                       as_of=date(2023, 6, 1)).status == "CONTRADICTED"


def test_numeric_annual_language_on_quarterly_fact_contradicted(tmp_path):
    """Pilot-run finding (2026-08-21): the first free-model memo quoted
    Apple's Dec-2022 quarter ($117.15B) as "fiscal year" revenue. Value and
    citation matched, so the judge said SUPPORTED. Period wording is part
    of the claim: annual language citing a ~90-day fact must contradict."""
    con = _con(tmp_path)
    _fact(con, fact_id="fQ", value=117_154_000_000.0)  # 89-day duration
    c = RawClaim(claim_text="Revenue for the fiscal year ending 2023-03-31 "
                            "was USD 117,154,000,000",
                 claim_type="NUMERIC", citations=["fQ"],
                 claimed_value=117_154_000_000.0)
    v = judge_claim(con, FakeLLM(), c, as_of=date(2023, 6, 1))
    assert v.status == "CONTRADICTED"
    assert "89 days" in v.reason


def test_numeric_annual_language_on_annual_fact_supported(tmp_path):
    con = _con(tmp_path)
    _fact(con, fact_id="fY", value=383_285_000_000.0,
          pstart=date(2022, 10, 1), pend=date(2023, 9, 30),
          filed=date(2023, 11, 3))
    c = RawClaim(claim_text="Fiscal year 2023 revenue was $383.3 billion",
                 claim_type="NUMERIC", citations=["fY"],
                 claimed_value=383.3e9)
    assert judge_claim(con, FakeLLM(), c,
                       as_of=date(2023, 12, 1)).status == "SUPPORTED"


def test_numeric_quarter_language_on_annual_fact_contradicted(tmp_path):
    con = _con(tmp_path)
    _fact(con, fact_id="fY", value=383_285_000_000.0,
          pstart=date(2022, 10, 1), pend=date(2023, 9, 30),
          filed=date(2023, 11, 3))
    c = RawClaim(claim_text="Q4 revenue was $383.3 billion",
                 claim_type="NUMERIC", citations=["fY"],
                 claimed_value=383.3e9)
    assert judge_claim(con, FakeLLM(), c,
                       as_of=date(2023, 12, 1)).status == "CONTRADICTED"


def test_numeric_period_check_skips_instant_facts(tmp_path):
    """Balance-sheet snapshots have no duration; "cash at fiscal year end"
    citing an instant fact is legitimate, not a period mislabel."""
    con = _con(tmp_path)
    _fact(con, fact_id="fI", field="cash_and_equivalents",
          value=29_965_000_000.0, ptype="instant",
          pstart=date(2023, 9, 30), pend=date(2023, 9, 30),
          filed=date(2023, 11, 3))
    c = RawClaim(claim_text="Cash at fiscal year end 2023 was $30.0B",
                 claim_type="NUMERIC", citations=["fI"],
                 claimed_value=30.0e9)
    assert judge_claim(con, FakeLLM(), c,
                       as_of=date(2023, 12, 1)).status == "SUPPORTED"


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


def test_temporal_leakage_excludes_fabricated_citation_ids(tmp_path):
    """Strongly-recommended test from the final whole-branch review:
    an unknown/fabricated citation id is guardrail territory (caught by
    citation_resolves), not a temporal leak. `visible_asof` reports it as
    "unknown identifier ..." and `temporal_leakage`'s `"unknown" not in
    problem` filter must keep excluding it — pinning the behavior so a
    future rewording of either message can't silently start counting
    fabrications as leaks in the MUST-be-zero headline metric."""
    con = _con(tmp_path)
    claims = [RawClaim(claim_text="x", claim_type="NUMERIC",
                       citations=["totally-fake-id"], claimed_value=1.0)]
    problems = temporal_leakage(con, claims, as_of=date(2023, 6, 1))
    assert problems == []


def test_temporal_leakage_excludes_derivation_integrity_failures(tmp_path):
    """I4/visibility split: a derivation whose stored value was tampered
    with is a guardrail (integrity) violation, not a temporal leak — the
    MUST-be-zero gate must not be contaminated by it. See
    test_guardrails.test_derivation_recompute_checked for the guardrail
    side of this same tamper."""
    con = _con(tmp_path)
    _fact(con, fact_id="gp", field="gross_profit", value=40.0)
    _fact(con, fact_id="rev", field="revenue", value=100.0)
    d = compute(con, "gp / rev", {"gp": "gp", "rev": "rev"},
               date(2023, 6, 1))
    con.execute("UPDATE derivation SET value = 0.9 WHERE derivation_id = ?",
               [d.derivation_id])
    claims = [RawClaim(claim_text="Gross margin was 40%",
                       claim_type="DERIVED", citations=[d.derivation_id],
                       claimed_value=0.40)]
    problems = temporal_leakage(con, claims, as_of=date(2023, 6, 1))
    assert problems == []


def test_numeric_claim_citing_derivation_judged_as_derived(tmp_path):
    """First real eval (2026-08-23): the decomposer typed margin claims
    NUMERIC, so their D- citations were looked up in the fact table and
    five correct claims scored UNSUPPORTED. Decomposer type labels are
    fuzzy model output; the citation's id prefix is deterministic — a
    claim citing a derivation is judged down the derivation path."""
    con = _con(tmp_path)
    _fact(con, fact_id="gp", field="gross_profit", value=45.9)
    _fact(con, fact_id="rev", field="revenue", value=100.0)
    d = compute(con, "gp / rev", {"gp": "gp", "rev": "rev"},
                date(2023, 6, 1))
    c = RawClaim(claim_text="Gross margin was 45.9%.",
                 claim_type="NUMERIC", citations=[d.derivation_id],
                 claimed_value=0.459)   # "45.9%" normalised at extraction
    v = judge_claim(con, FakeLLM(), c, as_of=date(2023, 6, 1))
    assert v.status == "SUPPORTED"


def test_numeric_tolerance_is_bounded_no_blind_scale_trials(tmp_path):
    """Pilot claim 14 (2026-08-23): a 3.1-point margin CHANGE (0.031) was
    marked SUPPORTED against a 30.74% margin LEVEL, because 0.031 x 1000
    lands within tolerance of 30.74 once every unit scale is tried. Values
    are normalised at extraction now (spec §8.0), so scoring compares
    directly and this must contradict."""
    con = _con(tmp_path)
    _fact(con, fact_id="oi", field="operating_income", value=30.7424)
    _fact(con, fact_id="rev", field="revenue", value=100.0)
    d = compute(con, "oi / rev", {"oi": "oi", "rev": "rev"},
                date(2023, 6, 1))
    c = RawClaim(claim_text="Operating margin improved 3.1 points",
                 claim_type="DERIVED", citations=[d.derivation_id],
                 claimed_value=0.031)
    assert judge_claim(con, FakeLLM(), c,
                       as_of=date(2023, 6, 1)).status == "CONTRADICTED"


def test_numeric_still_matches_a_sign_flipped_loss(tmp_path):
    """Losses are routinely quoted positive in prose."""
    con = _con(tmp_path)
    _fact(con, fact_id="fLoss", field="net_income", value=-2.5e9)
    c = RawClaim(claim_text="Net loss was $2.5 billion", claim_type="NUMERIC",
                 citations=["fLoss"], claimed_value=2.5e9)
    assert judge_claim(con, FakeLLM(), c,
                       as_of=date(2023, 6, 1)).status == "SUPPORTED"


def test_comparative_verifies_both_endpoints(tmp_path):
    """A change stated from two cited levels needs no derivation_id: each
    cited value must appear among the numbers the claim states. The old
    scorer marked these UNSUPPORTED for 'cites no derivation_id' (pilot
    claims 20 and 27)."""
    con = _con(tmp_path)
    _fact(con, fact_id="fPrior", value=394.328e9)
    _fact(con, fact_id="fNow", value=383.285e9)
    c = RawClaim(claim_text="Revenue declined from $394.3 billion to "
                            "$383.3 billion",
                 claim_type="COMPARATIVE", citations=["fPrior", "fNow"],
                 claimed_values=[394.3e9, 383.3e9])
    assert judge_claim(con, FakeLLM(), c,
                       as_of=date(2023, 6, 1)).status == "SUPPORTED"


def test_comparative_contradicted_when_an_endpoint_is_wrong(tmp_path):
    con = _con(tmp_path)
    _fact(con, fact_id="fPrior", value=394.328e9)
    _fact(con, fact_id="fNow", value=383.285e9)
    c = RawClaim(claim_text="Revenue declined from $394.3 billion to "
                            "$340.0 billion",
                 claim_type="COMPARATIVE", citations=["fPrior", "fNow"],
                 claimed_values=[394.3e9, 340.0e9])
    v = judge_claim(con, FakeLLM(), c, as_of=date(2023, 6, 1))
    assert v.status == "CONTRADICTED"
    assert "fNow" in v.reason
