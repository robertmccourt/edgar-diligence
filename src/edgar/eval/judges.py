import json
from datetime import date

from edgar.agent.llm import LLMClient
from edgar.eval.schemas import JudgeOpinion, RawClaim, Verdict
from edgar.tools.compute import ComputeError, recompute
from edgar.tools.visibility import visible_asof

_SCALES = (1.0, 1e3, 1e6, 1e9)
_JUDGE_SYSTEM = ("You judge whether evidence supports a claim. "
                 "Answer with status SUPPORTED, PARTIALLY_SUPPORTED, "
                 "UNSUPPORTED, or CONTRADICTED, and a one-sentence reason. "
                 "Be strict: the evidence must actually say it.")


def _match(claimed: float | None, actual: float, tol: float) -> bool:
    if claimed is None:
        return False
    return any(abs(claimed * s - actual) <= tol * max(1.0, abs(actual))
               for s in _SCALES) or \
        any(abs(claimed * s + actual) <= tol * max(1.0, abs(actual))
            for s in _SCALES)   # sign conventions: |loss| quoted positive


def _first_of(con, claim: RawClaim, table: str, col: str, cols: str):
    for cid in claim.citations:
        row = con.execute(
            f"SELECT {cols} FROM {table} WHERE {col} = ?", [cid]).fetchone()
        if row is not None:
            return cid, row
    return None, None


def judge_claim(con, llm: LLMClient, claim: RawClaim, as_of: date,
                tolerance: float = 0.02) -> Verdict:
    t = claim.claim_type
    if t == "UNSUPPORTED" or not claim.citations:
        return Verdict(claim=claim, status="UNSUPPORTED",
                       reason="no citation attached")
    if t == "NUMERIC":
        cid, row = _first_of(con, claim, "fact", "fact_id", "value")
        if row is None:
            return Verdict(claim=claim, status="UNSUPPORTED",
                           reason=f"citations {claim.citations} resolve "
                                  "to no fact")
        ok = _match(claim.claimed_value, row[0], tolerance)
        return Verdict(claim=claim,
                       status="SUPPORTED" if ok else "CONTRADICTED",
                       reason=f"fact {cid} value {row[0]:g} vs claimed "
                              f"{claim.claimed_value}")
    if t == "DERIVED":
        cid = next((c for c in claim.citations if c.startswith("D-")), None)
        if cid is None:
            return Verdict(claim=claim, status="UNSUPPORTED",
                           reason="derived claim cites no derivation_id")
        try:
            comp = recompute(con, cid)
        except ComputeError as exc:
            return Verdict(claim=claim, status="CONTRADICTED",
                           reason=f"derivation fails to recompute: {exc}")
        ok = _match(claim.claimed_value, comp.value, tolerance) or \
            _match(claim.claimed_value, comp.value * 100, tolerance) or \
            _match(claim.claimed_value, comp.value * 10_000, tolerance)
        # x100 / x10000: percent and bps phrasings of the same ratio
        return Verdict(claim=claim,
                       status="SUPPORTED" if ok else "CONTRADICTED",
                       reason=f"derivation {cid} = {comp.value:g} vs "
                              f"claimed {claim.claimed_value}")
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
