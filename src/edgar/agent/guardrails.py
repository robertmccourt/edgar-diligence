import re
from datetime import date

import duckdb
from pydantic import BaseModel

from edgar.agent.memo import Claim, Memo
from edgar.tools.compute import ComputeError, recompute

_NUMERIC = re.compile(
    r"(\d[\d,.]*\s*(%|bps|bn|billion|million|x\b)|[$€£]\s*\d|\d{3,})",
    re.IGNORECASE)


def _is_numeric_claim(text: str) -> bool:
    return bool(_NUMERIC.search(text))


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
    """Return None when the id exists and is visible as of as_of,
    else a human-readable problem."""
    if cid.startswith("D-"):
        row = con.execute("SELECT as_of, value FROM derivation "
                          "WHERE derivation_id = ?", [cid]).fetchone()
        if row is None:
            return f"unknown derivation {cid}"
        if row[0] > as_of:
            return f"derivation {cid} computed for later as_of {row[0]}"
        try:
            again = recompute(con, cid)
        except ComputeError as exc:
            return f"derivation {cid} does not recompute: {exc}"
        if abs(again.value - row[1]) > 1e-9 * max(1.0, abs(row[1])):
            return (f"derivation {cid} stored {row[1]} but recomputes "
                    f"to {again.value}")
        return None
    for table, col in (("fact", "fact_id"), ("span", "span_id")):
        row = con.execute(
            f"SELECT filed_date FROM {table} WHERE {col} = ?",
            [cid]).fetchone()
        if row is not None:
            return None if row[0] <= as_of else \
                f"{table} {cid} filed {row[0]} after as_of {as_of}"
    return f"unknown identifier {cid}"


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
    return fixed
