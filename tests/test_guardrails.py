from datetime import date

from edgar.db import connect
from edgar.curate.facts import create_fact_table
from edgar.narrative.store import create_narrative_tables
from edgar.tools.compute import compute, create_derivation_table
from edgar.agent.memo import Claim, Memo, MemoSection
from edgar.agent.guardrails import _is_numeric_claim, check_memo, repair_memo


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


def _memo(claims, hypotheses=()):
    return Memo(cik=1, company_name="ACME", as_of=date(2023, 6, 1),
                sections=[MemoSection(slug="growth", title="Growth",
                                      claims=list(claims))],
                hypotheses=list(hypotheses))


def test_numeric_claim_detector():
    assert _is_numeric_claim("Revenue was $2.1B")
    assert _is_numeric_claim("margin fell 240 bps")
    assert not _is_numeric_claim("Management discussed freight costs")


def test_uncited_numeric_claim_is_violation_and_repair_downgrades(tmp_path):
    con = _con(tmp_path)
    memo = _memo([Claim(text="Revenue was 100")])
    rep = check_memo(con, memo, date(2023, 6, 1))
    assert rep.rejection_count == 1
    assert rep.violations[0].rule == "needs_citation"
    fixed = repair_memo(memo, rep)
    c = fixed.sections[0].claims[0]
    assert c.is_hypothesis and c.text.startswith("[UNVERIFIED")


def test_citation_must_resolve_and_respect_as_of(tmp_path):
    con = _con(tmp_path)
    _fact(con, fact_id="fA", filed=date(2023, 5, 1))
    _fact(con, fact_id="fLate", filed=date(2024, 5, 1))
    ok = _memo([Claim(text="Revenue was 100", citations=["fA"])])
    assert check_memo(con, ok, date(2023, 6, 1)).rejection_count == 0
    ghost = _memo([Claim(text="Revenue was 100", citations=["nope"])])
    assert check_memo(con, ghost, date(2023, 6, 1)).violations[0].rule == \
        "citation_resolves"
    leak = _memo([Claim(text="Revenue was 100", citations=["fLate"])])
    assert check_memo(con, leak, date(2023, 6, 1)).violations[0].rule == \
        "citation_resolves"


def test_derivation_recompute_checked(tmp_path):
    con = _con(tmp_path)
    _fact(con, fact_id="gp", field="gross_profit", value=40.0)
    _fact(con, fact_id="rev", field="revenue", value=100.0)
    c = compute(con, "gp / rev", {"gp": "gp", "rev": "rev"},
                date(2023, 6, 1))
    good = _memo([Claim(text="Gross margin was 40%",
                        citations=[c.derivation_id])])
    assert check_memo(con, good, date(2023, 6, 1)).rejection_count == 0
    con.execute("UPDATE derivation SET value = 0.9 WHERE derivation_id = ?",
                [c.derivation_id])
    assert check_memo(con, good, date(2023, 6, 1)).violations[0].rule == \
        "derivation_recomputes"


def test_labeled_hypotheses_exempt_from_citation_rule(tmp_path):
    con = _con(tmp_path)
    memo = _memo([], hypotheses=[Claim(text="Margins sit 800 bps below "
                                            "peers",
                                       is_hypothesis=True)])
    assert check_memo(con, memo, date(2023, 6, 1)).rejection_count == 0
