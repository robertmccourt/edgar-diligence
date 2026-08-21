### Task 15: Eval — decomposition, typed judges, temporal check

Spec §8. The eval reads the RENDERED memo markdown — never the generator's internal structure — so the judge cannot inherit the generator's claim typing (that would be self-grading). Judge model: `claude-sonnet-5` via the same `LLMClient` seam; all tests offline with `FakeLLM`.

**Files:**
- Create: `src/edgar/eval/__init__.py` (empty), `src/edgar/eval/schemas.py`, `src/edgar/eval/decompose.py`, `src/edgar/eval/judges.py`
- Test: `tests/test_decompose.py`, `tests/test_judges.py`

**Interfaces:**

```python
# edgar.eval.schemas (all pydantic)
CLAIM_TYPES = ("NUMERIC", "DERIVED", "ATTRIBUTED", "INFERENTIAL", "UNSUPPORTED")
class RawClaim(BaseModel):
    claim_text: str
    claim_type: str                    # one of CLAIM_TYPES
    citations: list[str] = []          # ids the memo attached to this claim
    claimed_value: float | None = None # for NUMERIC/DERIVED, judge-extracted
class Decomposition(BaseModel):
    claims: list[RawClaim]
class Verdict(BaseModel):
    claim: RawClaim
    status: str        # SUPPORTED | PARTIALLY_SUPPORTED | UNSUPPORTED | CONTRADICTED
    reason: str
class JudgeOpinion(BaseModel):        # structured output of LLM judges
    status: str
    reason: str

# edgar.eval.decompose
decompose(llm: LLMClient, memo_markdown: str) -> list[RawClaim]
    # llm.parse_structured(output_model=Decomposition); prompt instructs:
    # atomic claims only (split compounds), copy [bracketed] ids into
    # citations verbatim, type per the §8.1 taxonomy, claims with no
    # citation -> UNSUPPORTED regardless of content.

# edgar.eval.judges
judge_claim(con, llm, claim: RawClaim, as_of: date,
            tolerance: float = 0.02) -> Verdict
    # dispatch on claim_type:
    # NUMERIC   -> deterministic: resolve first fact citation; compare
    #              claimed_value against fact value, scale-aware (see below)
    # DERIVED   -> deterministic: recompute cited D- id; compare claimed_value
    # ATTRIBUTED-> LLM: fetch cited span text; "does this text support the
    #              claim?" -> JudgeOpinion
    # INFERENTIAL-> LLM: premises = texts of cited ids; supported/contradicted
    # UNSUPPORTED-> Verdict(status="UNSUPPORTED", reason="no citation")
temporal_leakage(con, claims: list[RawClaim], as_of: date,
                 session_id: str | None = None) -> list[str]
    # problems: any cited id with visibility date > as_of (facts/spans/
    # derivations — reuse guardrails._visible), PLUS any conclusion id in
    # session.recalled_conclusion_ids whose learned_as_of > as_of.
```

Scale-aware numeric compare: prose says "2.1" (billions) while the fact stores 2_110_000_000. Accept the claimed value at any power-of-10 scale from {1, 1e3, 1e6, 1e9}: match if `min_over_scales(|claimed*s - actual| / max(1,|actual|)) <= tolerance`. Deterministic, no LLM.

- [ ] **Step 1: Failing tests**

`tests/test_decompose.py`:

```python
from edgar.agent.llm import FakeLLM
from edgar.eval.schemas import RawClaim, Decomposition
from edgar.eval.decompose import decompose

def test_decompose_passes_markdown_and_returns_claims():
    scripted = Decomposition(claims=[
        RawClaim(claim_text="Revenue was $2.1B", claim_type="NUMERIC",
                 citations=["fA"], claimed_value=2.1)])
    llm = FakeLLM(parsed=[scripted])
    claims = decompose(llm, "# memo\n- Revenue was $2.1B [fA]")
    assert claims[0].citations == ["fA"]
    assert "Revenue was $2.1B [fA]" in llm.parse_calls[0]["prompt"]

def test_decompose_prompt_demands_atomicity_and_verbatim_ids():
    llm = FakeLLM(parsed=[Decomposition(claims=[])])
    decompose(llm, "x")
    p = llm.parse_calls[0]["prompt"].lower()
    assert "atomic" in p and "verbatim" in p
```

`tests/test_judges.py`:

```python
import pytest
from datetime import date
from edgar.db import connect
from edgar.curate.facts import create_fact_table
from edgar.tools.compute import create_derivation_table, compute
from edgar.narrative.store import create_narrative_tables
from edgar.memory.episodic import create_memory_tables, save_session
from edgar.agent.llm import FakeLLM
from edgar.eval.schemas import RawClaim, JudgeOpinion
from edgar.eval.judges import judge_claim, temporal_leakage
# ... _fact helper ...

def _con(tmp_path):
    con = connect(tmp_path / "t.duckdb")
    create_fact_table(con); create_derivation_table(con)
    create_narrative_tables(con); create_memory_tables(con)
    return con

def test_numeric_supported_scale_aware(tmp_path):
    con = _con(tmp_path)
    _fact(con, fact_id="fA", value=2_110_000_000.0, filed=date(2023, 5, 1))
    c = RawClaim(claim_text="Revenue was $2.1B", claim_type="NUMERIC",
                 citations=["fA"], claimed_value=2.1)
    v = judge_claim(con, FakeLLM(), c, as_of=date(2023, 6, 1))
    assert v.status == "SUPPORTED"

def test_numeric_contradicted_when_value_off(tmp_path):
    con = _con(tmp_path)
    _fact(con, fact_id="fA", value=2_110_000_000.0, filed=date(2023, 5, 1))
    c = RawClaim(claim_text="Revenue was $3.4B", claim_type="NUMERIC",
                 citations=["fA"], claimed_value=3.4)
    assert judge_claim(con, FakeLLM(), c,
                       as_of=date(2023, 6, 1)).status == "CONTRADICTED"

def test_derived_recomputes_from_cited_derivation(tmp_path):
    con = _con(tmp_path)
    _fact(con, fact_id="gp", field="gross_profit", value=40.0)
    _fact(con, fact_id="rev", field="revenue", value=100.0)
    d = compute(con, "gp / rev", {"gp": "gp", "rev": "rev"}, date(2023, 6, 1))
    good = RawClaim(claim_text="Gross margin was 40%", claim_type="DERIVED",
                    citations=[d.derivation_id], claimed_value=0.40)
    assert judge_claim(con, FakeLLM(), good,
                       as_of=date(2023, 6, 1)).status == "SUPPORTED"

def test_attributed_uses_llm_over_span_text(tmp_path):
    con = _con(tmp_path)
    con.execute("INSERT INTO span VALUES ('sB','d1',1,'a-1','10-K','Item 7',"
                "DATE '2023-05-01',0,40,'Freight costs pressured margins.',"
                "NULL)")
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
```

- [ ] **Step 2: Run to verify failure** — ImportError expected.

- [ ] **Step 3: Implement**

`src/edgar/eval/schemas.py`: exactly the models above (plus `CLAIM_TYPES`, and a pydantic `field_validator` on `claim_type`/`status` restricting to the allowed literals).

`src/edgar/eval/decompose.py`:

```python
from edgar.agent.llm import LLMClient
from edgar.eval.schemas import Decomposition, RawClaim

_SYSTEM = "You are a claim auditor for financial memos. You are precise and literal."

_PROMPT = """Decompose the memo below into ATOMIC claims — one checkable \
assertion each; split compound sentences. For each claim:
- copy any [bracketed] citation identifiers into `citations` VERBATIM
- set claim_type: NUMERIC (states a specific figure), DERIVED (states a \
computed quantity: growth, margin, ratio, delta), ATTRIBUTED (reports what \
management or the filing SAYS), INFERENTIAL (an interpretation or judgment), \
UNSUPPORTED (carries no citation — regardless of content)
- for NUMERIC/DERIVED set claimed_value to the number as written (e.g. \
"$2.1B" -> 2.1; "240 bps" -> 240; "12%" -> 0.12)
Skip headings, the as-of banner, and status-code lines. Do not paraphrase.

MEMO:
{memo}
"""


def decompose(llm: LLMClient, memo_markdown: str) -> list[RawClaim]:
    out = llm.parse_structured(system=_SYSTEM,
                               prompt=_PROMPT.format(memo=memo_markdown),
                               output_model=Decomposition)
    return out.claims
```

`src/edgar/eval/judges.py`:

```python
import json
from datetime import date
from edgar.agent.llm import LLMClient
from edgar.agent.guardrails import _visible
from edgar.eval.schemas import RawClaim, Verdict, JudgeOpinion
from edgar.tools.compute import recompute, ComputeError

_SCALES = (1.0, 1e3, 1e6, 1e9)
_JUDGE_SYSTEM = ("You judge whether evidence supports a claim. "
                 "Answer with status SUPPORTED, PARTIALLY_SUPPORTED, "
                 "UNSUPPORTED, or CONTRADICTED, and a one-sentence reason. "
                 "Be strict: the evidence must actually say it.")


def _match(claimed: float, actual: float, tol: float) -> bool:
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
                           reason=f"citations {claim.citations} resolve to "
                                  "no fact")
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
        # ×100 / ×10000: percent and bps phrasings of the same ratio
        return Verdict(claim=claim,
                       status="SUPPORTED" if ok else "CONTRADICTED",
                       reason=f"derivation {cid} = {comp.value:g} vs claimed "
                              f"{claim.claimed_value}")
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
               "inference? CONTRADICTED only if premises actively conflict.",
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
            problem = _visible(con, cid, as_of)
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
```

- [ ] **Step 4: Run** — both test files → PASS; full suite.

- [ ] **Step 5: Commit**

```bash
git add src/edgar/eval tests/test_decompose.py tests/test_judges.py
git commit -m "feat(eval): decomposition + typed judges + dual-surface temporal check

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

