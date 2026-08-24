from edgar.eval.metrics import compute_metrics, to_markdown
from edgar.eval.schemas import RawClaim, Verdict


def _v(text, ctype, status, cites=("x",)):
    return Verdict(claim=RawClaim(claim_text=text, claim_type=ctype,
                                  citations=list(cites)),
                   status=status, reason="r")


_META = {"memo_path": "m.json", "config_version": "v1+deadbeef",
         "session_id": "S1", "as_of": "2023-06-01",
         "guardrail_rejections": 3}


def test_rates_and_breakdowns():
    verdicts = [
        _v("a", "NUMERIC", "SUPPORTED"),
        _v("b", "DERIVED", "CONTRADICTED"),
        _v("c", "UNSUPPORTED", "UNSUPPORTED", cites=()),
        _v("d", "ATTRIBUTED", "SUPPORTED"),
    ]
    r = compute_metrics(verdicts, ["leak-1"], memo_meta=_META)
    assert r.n_claims == 4
    assert r.unsupported_rate == 0.25
    assert r.contradiction_rate == 0.25
    assert r.citation_coverage == 0.75
    assert r.by_type["NUMERIC"] == 1 and r.by_status["SUPPORTED"] == 2
    assert r.temporal_leakage_count == 1
    assert r.guardrail_rejections == 3


def test_markdown_report_carries_the_headline_numbers():
    r = compute_metrics([_v("a", "NUMERIC", "SUPPORTED")], [],
                        memo_meta=_META)
    md = to_markdown(r)
    assert "unsupported" in md.lower() and "v1+deadbeef" in md
    assert "temporal" in md.lower()


def test_empty_memo_does_not_divide_by_zero():
    r = compute_metrics([], [], memo_meta=_META)
    assert r.n_claims == 0 and r.unsupported_rate == 0.0


def test_hypotheses_excluded_from_unsupported_denominator():
    """Spec 7.7 requires speculation to be stated under the hypotheses
    heading without citations; the first paid eval then counted all three
    as unsupported claims, penalising the memo for obeying the spec
    (rev 3c). They are reported separately instead."""
    verdicts = [
        Verdict(claim=RawClaim(claim_text="Revenue was $1B",
                               claim_type="NUMERIC", citations=["f1"],
                               claimed_value=1e9),
                status="SUPPORTED", reason="ok"),
        Verdict(claim=RawClaim(claim_text="Buybacks could rise",
                               claim_type="INFERENTIAL", is_hypothesis=True),
                status="UNSUPPORTED", reason="no citation attached"),
        Verdict(claim=RawClaim(claim_text="Margins could expand",
                               claim_type="INFERENTIAL", is_hypothesis=True),
                status="UNSUPPORTED", reason="no citation attached"),
    ]
    r = compute_metrics(verdicts, [], memo_meta=_META)
    assert r.n_claims == 1
    assert r.n_hypotheses == 2
    assert r.unsupported_rate == 0.0
    assert r.citation_coverage == 1.0


def test_narrative_gaps_reported():
    r = compute_metrics([], [], memo_meta=_META,
                        narrative_gaps=["Profitability: narrative states "
                                        "25.6% but no cited bullet"])
    assert r.narrative_gap_count == 1
    assert "25.6%" in to_markdown(r)
