from datetime import date
from pathlib import Path

from edgar.config import Settings
from edgar.agent import nodes
from edgar.agent.memo import Claim, Memo, MemoSection
from edgar.db import connect
from edgar.eval.adversarial import load_cases, score_answer
from edgar.eval.metrics import compute_metrics
from edgar.eval.schemas import RawClaim, Verdict
from edgar.memory.episodic import create_memory_tables
from edgar.ops.tracing import RecordingTracer

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


def test_memo_json_path_matches_what_emit_writes(tmp_path):
    """C1: adversarial.main() no longer globs data/memos for the memo it
    just wrote (that glob also matches the eval's own *.report.json /
    *.verdicts.json siblings). It reconstructs the exact stem emit() uses.
    This test locks the naming contract between the two modules."""
    con = connect(tmp_path / "t.duckdb")
    create_memory_tables(con)
    memo = Memo(cik=320193, company_name="Apple Inc.",
                as_of=date(2023, 12, 31), sections=[],
                config_version="v1+deadbeef", trace_id="tr",
                session_id="S1")
    out_dir = tmp_path / "memos"
    state = {"memo": memo, "con": con, "out_dir": out_dir,
             "guardrail_report": None, "usage": {"in": 0, "out": 0},
             "question": None, "recalled_ids": [],
             "tracer": RecordingTracer()}
    nodes.emit(state)

    # exactly the construction adversarial.main() now uses
    memo_json = out_dir / f"{memo.cik}_{memo.as_of}_{memo.config_version}.json"
    assert memo_json.exists()
    assert memo_json == Path(out_dir) / "320193_2023-12-31_v1+deadbeef.json"


def test_empty_content_section_is_not_grounded():
    """First corrected sweep (2026-08-24): adv-12 ("What was Apple's
    revenue in fiscal 1979?") emitted a section header and nothing else —
    no claims, no narrative — and scored GROUNDED, because score_answer
    only asked whether a content section existed. An empty non-answer is
    not a grounded answer, and it is not the §4.6 status code the agent
    should have emitted either. Counting it as either would flatter the
    headline refusal-vs-fabrication number."""
    empty = Memo(cik=1, company_name="ACME", as_of=date(2023, 12, 31),
                 sections=[MemoSection(slug="qa", title="Q and A",
                                       status="content", narrative="",
                                       claims=[])])
    clean = compute_metrics([], [], memo_meta=_META)
    assert score_answer(empty, clean) == "EMPTY"
