import json
import re
from datetime import date

from edgar.agent.llm import LLMClient
from edgar.eval.schemas import JudgeOpinion, RawClaim, Verdict
from edgar.tools.compute import ComputeError, recompute
from edgar.tools.visibility import visible_asof

_JUDGE_SYSTEM = ("You judge whether evidence supports a claim. "
                 "Answer with status SUPPORTED, PARTIALLY_SUPPORTED, "
                 "UNSUPPORTED, or CONTRADICTED, and a one-sentence reason. "
                 "Be strict: the evidence must actually say it.")


_QUARTER_LANG = re.compile(r"\bq[1-4]\b|\bquarter|\bthree[- ]months?", re.I)
_ANNUAL_LANG = re.compile(
    r"\bfiscal[- ](?:year|\d{4})|\bfull[- ]year|\bannual"
    r"|\byear[- ]end(?:ed|ing)?\b|\bfy\s?'?\d{2,4}\b|\btwelve[- ]months?",
    re.I)


def _period_mismatch(text: str, period_type: str,
                     start: date | None, end: date | None) -> str | None:
    """Pilot-run finding (2026-08-21): a quarterly fact quoted as "fiscal
    year" revenue passed the value check — the number was real and
    correctly cited, only the period wording was wrong. Period language is
    part of the claim. Instant facts are exempt: "cash at fiscal year end"
    citing a balance-sheet snapshot is legitimate. Quarterly language is
    checked first — "Q4 of fiscal 2023" claims a quarter."""
    if period_type != "duration" or start is None or end is None:
        return None
    days = (end - start).days
    if _QUARTER_LANG.search(text):
        if days > 120:
            return (f"claim says quarterly but cited fact spans {days} "
                    f"days ({start}..{end})")
        return None
    if _ANNUAL_LANG.search(text) and days < 300:
        return (f"claim says annual/fiscal-year but cited fact spans "
                f"{days} days ({start}..{end})")
    return None


def _match(claimed: float | None, actual: float, tol: float) -> bool:
    """Direct comparison at a bounded tolerance. Extraction normalises a
    claim's value to base units using the claim's own wording (spec §8.0),
    so the scorer no longer guesses at scale. The blind trial this
    replaces — every power of ten, plus x100/x10000 — passed a 3.1-point
    margin change as a 30.7% margin level (pilot claim 14). The one
    remaining allowance is a sign flip: losses are quoted positive."""
    if claimed is None:
        return False
    room = tol * max(abs(actual), 1e-9)
    return abs(claimed - actual) <= room or abs(claimed + actual) <= room


def _first_of(con, claim: RawClaim, table: str, col: str, cols: str):
    for cid in claim.citations:
        row = con.execute(
            f"SELECT {cols} FROM {table} WHERE {col} = ?", [cid]).fetchone()
        if row is not None:
            return cid, row
    return None, None


def _judge_derived(con, claim: RawClaim, tolerance: float) -> Verdict:
    cid = next((c for c in claim.citations if c.startswith("D-")), None)
    if cid is None:
        return Verdict(claim=claim, status="UNSUPPORTED",
                       reason="derived claim cites no derivation_id")
    try:
        comp = recompute(con, cid)
    except ComputeError as exc:
        return Verdict(claim=claim, status="CONTRADICTED",
                       reason=f"derivation fails to recompute: {exc}")
    ok = _match(claim.claimed_value, comp.value, tolerance)
    return Verdict(claim=claim,
                   status="SUPPORTED" if ok else "CONTRADICTED",
                   reason=f"derivation {cid} = {comp.value:g} vs "
                          f"claimed {claim.claimed_value}")


def _resolve_level(con, cid: str) -> float | None:
    """The value behind a cited level id — a stored fact, or a derivation
    replayed from its inputs."""
    if cid.startswith("D-"):
        try:
            return recompute(con, cid).value
        except ComputeError:
            return None
    row = con.execute("SELECT value FROM fact WHERE fact_id = ?",
                      [cid]).fetchone()
    return row[0] if row else None


def _judge_comparative(con, claim: RawClaim, tolerance: float) -> Verdict:
    """A change described from its endpoints. The memo cites the levels and
    states the numbers; there is no computed quantity, so demanding a
    derivation_id is a scorer defect (spec §8.1, rev 3c). Every cited level
    must appear among the stated numbers, order-independent."""
    resolved = {cid: _resolve_level(con, cid) for cid in claim.citations}
    unresolved = [cid for cid, v in resolved.items() if v is None]
    if unresolved:
        return Verdict(claim=claim, status="UNSUPPORTED",
                       reason=f"citations {unresolved} resolve to no "
                              "fact or derivation")
    stated = claim.claimed_values or (
        [claim.claimed_value] if claim.claimed_value is not None else [])
    unmatched = [
        f"{cid}={value:g}" for cid, value in resolved.items()
        if not any(_match(s, value, tolerance) for s in stated)]
    if unmatched:
        return Verdict(claim=claim, status="CONTRADICTED",
                       reason=f"cited {', '.join(unmatched)} appears among "
                              f"none of the stated values {stated}")
    return Verdict(claim=claim, status="SUPPORTED",
                   reason=f"every cited level matches a stated value "
                          f"({len(resolved)} endpoints checked)")


def judge_claim(con, llm: LLMClient, claim: RawClaim, as_of: date,
                tolerance: float = 0.02) -> Verdict:
    t = claim.claim_type
    if t == "UNSUPPORTED" or not claim.citations:
        return Verdict(claim=claim, status="UNSUPPORTED",
                       reason="no citation attached")
    if t == "NUMERIC":
        cid, row = _first_of(con, claim, "fact", "fact_id",
                             "value, period_type, period_start, period_end")
        if row is None:
            if any(c.startswith("D-") for c in claim.citations):
                # Decomposer type labels are fuzzy model output; the id
                # prefix is deterministic. A margin/ratio claim typed
                # NUMERIC but citing a derivation is judged as DERIVED
                # (first real eval scored five correct margin claims
                # UNSUPPORTED by looking D- ids up in the fact table).
                return _judge_derived(con, claim, tolerance)
            return Verdict(claim=claim, status="UNSUPPORTED",
                           reason=f"citations {claim.citations} resolve "
                                  "to no fact")
        if not _match(claim.claimed_value, row[0], tolerance):
            return Verdict(claim=claim, status="CONTRADICTED",
                           reason=f"fact {cid} value {row[0]:g} vs claimed "
                                  f"{claim.claimed_value}")
        mismatch = _period_mismatch(claim.claim_text, row[1], row[2], row[3])
        if mismatch:
            return Verdict(claim=claim, status="CONTRADICTED",
                           reason=f"fact {cid}: {mismatch}")
        return Verdict(claim=claim, status="SUPPORTED",
                       reason=f"fact {cid} value {row[0]:g} vs claimed "
                              f"{claim.claimed_value}")
    if t == "DERIVED":
        return _judge_derived(con, claim, tolerance)
    if t == "COMPARATIVE":
        return _judge_comparative(con, claim, tolerance)
    if t == "ATTRIBUTED":
        cid, row = _first_of(con, claim, "span", "span_id", "text")
        if row is None:
            return Verdict(claim=claim, status="UNSUPPORTED",
                           reason="citations resolve to no span")
        op = llm.parse_structured(
            system=_JUDGE_SYSTEM,
            prompt=f"CLAIM: {claim.claim_text}\n\nEVIDENCE (span {cid}):\n"
                   f"{row[0]}",
            output_model=JudgeOpinion)
        return Verdict(claim=claim, status=op.status, reason=op.reason)
    # INFERENTIAL: premises are whatever the citations resolve to
    premises = []
    for cid in claim.citations:
        for table, col, cols in (("fact", "fact_id",
                                  "canonical_field, value, period_end"),
                                 ("span", "span_id", "text"),
                                 ("derivation", "derivation_id",
                                  "expression, value")):
            row = con.execute(f"SELECT {cols} FROM {table} WHERE {col} = ?",
                              [cid]).fetchone()
            if row is not None:
                premises.append(f"[{cid}] {row}")
    if not premises:
        return Verdict(claim=claim, status="UNSUPPORTED",
                       reason="no citation resolves")
    op = llm.parse_structured(
        system=_JUDGE_SYSTEM,
        prompt=f"CLAIM (an inference): {claim.claim_text}\n\nPREMISES:\n" +
               "\n".join(premises) +
               "\n\nAre the premises sufficient and consistent with the "
               "inference? CONTRADICTED only if premises actively "
               "conflict.",
        output_model=JudgeOpinion)
    return Verdict(claim=claim, status=op.status, reason=op.reason)


def temporal_leakage(con, claims: list[RawClaim], as_of: date,
                     session_id: str | None = None) -> list[str]:
    problems: list[str] = []
    seen: set[str] = set()
    for claim in claims:
        for cid in claim.citations:
            if cid in seen:
                continue
            seen.add(cid)
            problem = visible_asof(con, cid, as_of)
            if problem is not None and "unknown" not in problem:
                problems.append(problem)
    if session_id:
        row = con.execute("SELECT recalled_conclusion_ids FROM session "
                          "WHERE session_id = ?", [session_id]).fetchone()
        for cid in json.loads(row[0]) if row and row[0] else []:
            r = con.execute("SELECT learned_as_of FROM session_conclusion "
                            "WHERE conclusion_id = ?", [cid]).fetchone()
            if r and r[0] > as_of:
                problems.append(f"recalled conclusion {cid} learned "
                                f"{r[0]} after as_of {as_of}")
    return problems
