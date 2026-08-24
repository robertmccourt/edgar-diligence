"""Deterministic claim extraction (spec §8.0, rev 3c).

The memo is a structured artifact this system emits — one assertion per
bullet, ids in [brackets]. Extraction parses it; no model is involved, so
the same memo always yields the same claims.
"""
import pytest

from edgar.eval.extract import extract_claims, narrative_gaps, parse_values

MEMO = """# Diligence memo: APPLE INC (CIK 320193)
*As of 2024-03-01 — only information filed on or before this date was used.*
*config deepseek+c9f2e908 · trace trace-1*

## Business description
Apple designs and markets smartphones.

- The Company designs, manufactures and markets smartphones. [sp01]
- Competitors imitate the Company's products. [sp02]

## Profitability
Gross margin expanded to 45.9% and net margin rose to 28.4%.

- In the quarter ended December 31, 2023, revenue was $119.6 billion. [fa01]
- Gross margin for the same quarter was 45.9%. [D-aa01]
- Gross margin expanded from 43.0% to 45.9%. [D-bb02] [D-aa01]
- Revenue grew from $117.2 billion to $119.6 billion. [fa02] [fa01]
- Margins improved across every product line.

## Value-creation hypotheses
_Hypothesis, not established by the data (spec §7.7)._
- **Hypothesis:** Apple could increase its share repurchase program.
- **Hypothesis:** Services expansion could lift profitability. [fa01]
"""

KINDS = {"fa01": "fact", "fa02": "fact", "sp01": "span", "sp02": "span"}


def _kind(cid: str) -> str:
    if cid.startswith("D-"):
        return "derivation"
    return KINDS.get(cid, "unknown")


def _claims():
    return extract_claims(MEMO, _kind)


def test_one_bullet_one_claim_no_narrative_shredding():
    claims = _claims()
    assert len(claims) == 9
    texts = [c.claim_text for c in claims]
    # narrative prose is not turned into pseudo-claims
    assert "Gross margin expanded to 45.9% and net margin rose" not in texts


def test_citations_copied_verbatim_and_stripped_from_text():
    c = _claims()[0]
    assert c.citations == ["sp01"]
    assert c.claim_text == ("The Company designs, manufactures and markets "
                            "smartphones.")


def test_extraction_is_deterministic():
    assert [c.model_dump() for c in extract_claims(MEMO, _kind)] == \
           [c.model_dump() for c in extract_claims(MEMO, _kind)]


def _typed(prefix: str) -> str:
    return next(c.claim_type for c in _claims()
                if c.claim_text.startswith(prefix))


def test_type_follows_cited_id_kind():
    assert _typed("The Company designs") == "ATTRIBUTED"
    assert _typed("In the quarter ended") == "NUMERIC"
    assert _typed("Gross margin for the same") == "DERIVED"
    assert _typed("Margins improved") == "UNSUPPORTED"


def test_two_levels_two_numbers_is_comparative_not_derived():
    """A change stated from two cited level facts is a COMPARATIVE. The
    old scorer typed these DERIVED and then demanded a derivation_id the
    memo was right not to invent (pilot claims 20 and 27)."""
    comps = [c for c in _claims() if c.claim_type == "COMPARATIVE"]
    assert len(comps) == 2
    assert {tuple(c.citations) for c in comps} == {
        ("D-bb02", "D-aa01"), ("fa02", "fa01")}


def test_hypotheses_are_flagged_and_prefix_stripped():
    hyps = [c for c in _claims() if c.is_hypothesis]
    assert len(hyps) == 2
    assert all(c.claim_type == "INFERENTIAL" for c in hyps)
    assert hyps[0].claim_text == ("Apple could increase its share "
                                  "repurchase program.")


def test_values_normalised_to_base_units():
    assert parse_values("revenue was $119.6 billion") == pytest.approx([119.6e9])
    assert parse_values("margin was 45.9%") == pytest.approx([0.459])
    assert parse_values("fell 240 bps") == pytest.approx([0.024])
    assert parse_values("ratio was 1.28x") == pytest.approx([1.28])
    assert parse_values("revenue of $383.3 billion") == pytest.approx([383.3e9])


def test_dates_are_not_mistaken_for_values():
    """Bare integers in dates carry no unit and must never become a
    claimed_value — the reason the old judge needed blind scale trials."""
    assert parse_values("In the quarter ended December 31, 2023") == []
    assert parse_values("As of 2024-03-01, cash was $40.76 billion") == \
        pytest.approx([40.76e9])


def test_comparative_keeps_every_stated_number():
    comp = next(c for c in _claims() if c.citations == ["fa02", "fa01"])
    assert comp.claimed_values == pytest.approx([117.2e9, 119.6e9])


def test_narrative_number_absent_from_bullets_is_flagged():
    """The one genuine finding of the 2026-08-23 audit: 25.6% appeared in
    a narrative paragraph with no cited bullet behind it. 45.9% does
    appear in a bullet, so only the unbacked figure is reported."""
    gaps = narrative_gaps(MEMO, _kind)
    assert len(gaps) == 1
    assert "28.4%" in gaps[0] and "Profitability" in gaps[0]
