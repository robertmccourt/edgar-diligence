import re
from datetime import date

import duckdb
from pydantic import BaseModel

from edgar.agent.memo import Claim, Memo
from edgar.tools.visibility import check_integrity, visible_asof

# Money / percent / bps / magnitude-word phrasings: always a numeric claim,
# regardless of any date-shaped text elsewhere in the sentence.
_MONEY_PCT = re.compile(
    r"(\d[\d,.]*\s*(%|bps|bn|billion|million|x\b)|[$€£]\s*\d)",
    re.IGNORECASE)
# Date-shaped text that must NOT by itself count as a numeric claim: ISO
# dates and bare 19xx/20xx years (e.g. "Fiscal 2023", "as of 2023-05-01").
_ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_BARE_YEAR = re.compile(r"\b(19|20)\d{2}\b")
# Any other 3+ digit run (e.g. "154000") still counts.
_LARGE_NUMBER = re.compile(r"\d{3,}")


def _is_numeric_claim(text: str) -> bool:
    if _MONEY_PCT.search(text):
        return True
    stripped = _BARE_YEAR.sub("", _ISO_DATE.sub("", text))
    return bool(_LARGE_NUMBER.search(stripped))


class Violation(BaseModel):
    section: str
    claim_text: str
    rule: str
    detail: str


class GuardrailReport(BaseModel):
    violations: list[Violation]
    checked_claims: int
    rejection_count: int


def _visible(con, cid: str, as_of: date) -> str | None:
    """Return None when the id exists, is visible as of as_of, AND (for a
    derivation) still recomputes cleanly — else a human-readable problem.
    Composes the date-only check with the integrity check; see
    `edgar.tools.visibility` for why those are two functions."""
    problem = visible_asof(con, cid, as_of)
    if problem is not None:
        return problem
    return check_integrity(con, cid)


def check_memo(con: duckdb.DuckDBPyConnection, memo: Memo,
               as_of: date) -> GuardrailReport:
    violations: list[Violation] = []
    checked = 0

    def _check(claim: Claim, section: str, citation_required: bool) -> None:
        nonlocal checked
        checked += 1
        if citation_required and not claim.citations and \
                _is_numeric_claim(claim.text):
            violations.append(Violation(
                section=section, claim_text=claim.text,
                rule="needs_citation",
                detail="numeric claim with no citation"))
            return
        for cid in claim.citations:
            problem = _visible(con, cid, as_of)
            if problem is None:
                continue
            rule = ("derivation_recomputes"
                    if cid.startswith("D-") and "recompute" in problem
                    else "citation_resolves")
            violations.append(Violation(section=section,
                                        claim_text=claim.text,
                                        rule=rule, detail=problem))

    for s in memo.sections:
        if s.status != "content":
            continue
        for claim in s.claims:
            _check(claim, s.slug, citation_required=not claim.is_hypothesis)
    for claim in memo.hypotheses:
        _check(claim, "hypotheses", citation_required=False)
    return GuardrailReport(violations=violations, checked_claims=checked,
                           rejection_count=len(violations))


def repair_memo(memo: Memo, report: GuardrailReport) -> Memo:
    """Downgrade, never delete (spec §7.3). Failing claims become labeled
    hypotheses in place; rejection stays visible in the memo itself."""
    bad = {(v.section, v.claim_text) for v in report.violations}
    fixed = memo.model_copy(deep=True)
    for s in fixed.sections:
        for c in s.claims:
            if (s.slug, c.text) in bad:
                c.is_hypothesis = True
                c.text = "[UNVERIFIED — downgraded by guardrail] " + c.text
                c.citations = []
    for c in fixed.hypotheses:
        if ("hypotheses", c.text) in bad:
            # already labeled speculation — no downgrade, just strip the
            # citations that don't hold up.
            c.text = "[UNVERIFIED — downgraded by guardrail] " + c.text
            c.citations = []
    return fixed
