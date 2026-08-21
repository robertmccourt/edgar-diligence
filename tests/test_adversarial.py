from datetime import date

from edgar.config import Settings
from edgar.agent.memo import Claim, Memo, MemoSection
from edgar.eval.adversarial import load_cases, score_answer
from edgar.eval.metrics import compute_metrics
from edgar.eval.schemas import RawClaim, Verdict

_META = {"memo_path": "m", "config_version": "v", "session_id": "S",
         "as_of": "2023-12-31", "guardrail_rejections": 0}


def test_thirty_unique_cases_over_narrative_ciks():
    cases = load_cases()
    assert len(cases) == 30
    assert len({c.id for c in cases}) == 30
    valid = set(Settings(_env_file=None).narrative_ciks)
    assert all(c.cik in valid for c in cases)
    assert {c.trap for c in cases} == {
        "unreported_metric", "pre_ipo_period",
        "nonexistent_segment", "post_asof_figure"}


def _memo(sections):
    return Memo(cik=320193, company_name="A", as_of=date(2023, 12, 31),
                sections=sections)


def test_refusal_grounded_fabricated():
    refused = _memo([MemoSection(slug="qa", title="Q&A",
                                 status="status_code",
                                 status_note="NOT_DISCLOSED")])
    clean = compute_metrics([], [], memo_meta=_META)
    assert score_answer(refused, clean) == "REFUSED"
    answered = _memo([MemoSection(slug="qa", title="Q&A",
                                  claims=[Claim(text="x",
                                                citations=["f"])])])
    good = compute_metrics(
        [Verdict(claim=RawClaim(claim_text="x", claim_type="NUMERIC",
                                citations=["f"]),
                 status="SUPPORTED", reason="r")], [], memo_meta=_META)
    assert score_answer(answered, good) == "GROUNDED"
    bad = compute_metrics(
        [Verdict(claim=RawClaim(claim_text="x", claim_type="UNSUPPORTED"),
                 status="UNSUPPORTED", reason="r")], [], memo_meta=_META)
    assert score_answer(answered, bad) == "FABRICATED"
