### Task 12: Memo models, renderer, output guardrails

Spec §7.3. The memo is a structured object first (checkable), markdown second (readable). Guardrails are deterministic — no LLM on the reply path — and rejections are counted, because the rejection rate is itself a reported metric.

**Files:**
- Create: `src/edgar/agent/memo.py`, `src/edgar/agent/guardrails.py`
- Test: `tests/test_memo.py`, `tests/test_guardrails.py`

**Interfaces:**

```python
# edgar.agent.memo
class Claim(BaseModel):
    text: str
    citations: list[str] = []          # fact_id / span_id / derivation_id
    is_hypothesis: bool = False

class MemoSection(BaseModel):
    slug: str; title: str
    status: str = "content"            # "content" | "status_code"
    narrative: str = ""                # section prose (may embed claim texts)
    claims: list[Claim] = []
    status_note: str = ""              # when status == "status_code"

class Memo(BaseModel):
    cik: int; company_name: str; as_of: date
    sections: list[MemoSection]
    hypotheses: list[Claim] = []       # value-creation ideas, labeled (spec §7.7)
    config_version: str = ""; trace_id: str = ""; session_id: str = ""

render_markdown(memo: Memo) -> str     # citations as [id] after each claim

# edgar.agent.guardrails
class Violation(BaseModel):
    section: str; claim_text: str; rule: str; detail: str

class GuardrailReport(BaseModel):
    violations: list[Violation]; checked_claims: int; rejection_count: int

check_memo(con, memo: Memo, as_of: date) -> GuardrailReport
repair_memo(memo: Memo, report: GuardrailReport) -> Memo
    # every violating claim -> is_hypothesis=True, text prefixed
    # "[UNVERIFIED — downgraded by guardrail] " ; never silently deleted
_is_numeric_claim(text: str) -> bool   # exported for the eval's reuse
```

Guardrail rules over every claim in every `content` section (and `hypotheses` are exempt from rule 1 — they are labeled speculation — but NOT from rules 2–3 when they do cite):
1. `needs_citation`: `_is_numeric_claim(text)` and `citations == []` → violation. Numeric-claim detector: contains a digit adjacent to `% $ bn bps million billion x` OR any bare number ≥ 3 chars, BUT NOT when the only digits belong to status codes / dates in `status_note` text. Keep it simple and slightly over-eager: over-flagging sends a claim to `compute`-backed citation, which is the behavior we want.
2. `citation_resolves`: each cited id exists in `fact`, `span`, or `derivation`, AND its visibility date (`filed_date` for fact/span; `as_of` column for derivation) is `<= as_of`. Unknown prefix/id → violation.
3. `derivation_recomputes`: for each cited `D-…` id, `recompute(con, id)` matches the stored value within `1e-9` relative. `ComputeError` → violation.

- [ ] **Step 1: Failing tests**

`tests/test_memo.py`:

```python
from datetime import date
from edgar.agent.memo import Claim, MemoSection, Memo, render_markdown

def _memo():
    return Memo(cik=1, company_name="ACME", as_of=date(2023, 6, 1),
                sections=[MemoSection(slug="growth", title="Growth",
                                      narrative="Revenue rose.",
                                      claims=[Claim(text="Revenue was 100",
                                                    citations=["fA"])]),
                          MemoSection(slug="working_capital",
                                      title="Working capital",
                                      status="status_code",
                                      status_note="inventory: NOT_DISCLOSED")],
                hypotheses=[Claim(text="Pricing lags peers",
                                  is_hypothesis=True)])

def test_render_includes_citations_and_status_codes():
    md = render_markdown(_memo())
    assert "## 2. Growth" not in md          # numbering comes from SECTIONS order,
    assert "## Growth" in md                 # renderer keeps titles simple
    assert "[fA]" in md
    assert "NOT_DISCLOSED" in md
    assert "Hypothesis" in md and "Pricing lags peers" in md

def test_render_shows_as_of_and_identity():
    md = render_markdown(_memo())
    assert "2023-06-01" in md and "ACME" in md
```

`tests/test_guardrails.py`:

```python
import pytest
from datetime import date
from edgar.db import connect
from edgar.curate.facts import create_fact_table
from edgar.tools.compute import create_derivation_table, compute
from edgar.narrative.store import create_narrative_tables
from edgar.agent.memo import Claim, MemoSection, Memo
from edgar.agent.guardrails import check_memo, repair_memo, _is_numeric_claim
# ... _fact helper ...

def _con(tmp_path):
    con = connect(tmp_path / "t.duckdb")
    create_fact_table(con); create_derivation_table(con)
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
    assert rep.rejection_count == 1 and rep.violations[0].rule == "needs_citation"
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
    c = compute(con, "gp / rev", {"gp": "gp", "rev": "rev"}, date(2023, 6, 1))
    good = _memo([Claim(text="Gross margin was 40%",
                        citations=[c.derivation_id])])
    assert check_memo(con, good, date(2023, 6, 1)).rejection_count == 0
    con.execute("UPDATE derivation SET value = 0.9 WHERE derivation_id = ?",
                [c.derivation_id])
    assert check_memo(con, good, date(2023, 6, 1)).violations[0].rule == \
        "derivation_recomputes"

def test_labeled_hypotheses_exempt_from_citation_rule(tmp_path):
    con = _con(tmp_path)
    memo = _memo([], hypotheses=[Claim(text="Margins sit 800 bps below peers",
                                       is_hypothesis=True)])
    assert check_memo(con, memo, date(2023, 6, 1)).rejection_count == 0
```

- [ ] **Step 2: Run to verify failure** — ImportError expected.

- [ ] **Step 3: Implement**

`src/edgar/agent/memo.py`:

```python
from datetime import date
from pydantic import BaseModel


class Claim(BaseModel):
    text: str
    citations: list[str] = []
    is_hypothesis: bool = False


class MemoSection(BaseModel):
    slug: str
    title: str
    status: str = "content"
    narrative: str = ""
    claims: list[Claim] = []
    status_note: str = ""


class Memo(BaseModel):
    cik: int
    company_name: str
    as_of: date
    sections: list[MemoSection]
    hypotheses: list[Claim] = []
    config_version: str = ""
    trace_id: str = ""
    session_id: str = ""


def render_markdown(memo: Memo) -> str:
    lines = [f"# Diligence memo: {memo.company_name} (CIK {memo.cik})",
             f"*As of {memo.as_of.isoformat()} — only information filed on or "
             f"before this date was used.*",
             f"*config {memo.config_version} · trace {memo.trace_id}*", ""]
    for s in memo.sections:
        lines.append(f"## {s.title}")
        if s.status == "status_code":
            lines += [f"_Not producible from the store:_ {s.status_note}", ""]
            continue
        if s.narrative:
            lines += [s.narrative, ""]
        for c in s.claims:
            cites = " ".join(f"[{i}]" for i in c.citations)
            lines.append(f"- {c.text} {cites}".rstrip())
        lines.append("")
    if memo.hypotheses:
        lines.append("## Value-creation hypotheses")
        lines.append("_Hypothesis, not established by the data (spec §7.7)._")
        for c in memo.hypotheses:
            cites = " ".join(f"[{i}]" for i in c.citations)
            lines.append(f"- **Hypothesis:** {c.text} {cites}".rstrip())
        lines.append("")
    return "\n".join(lines)
```

`src/edgar/agent/guardrails.py`:

```python
import re
from datetime import date
import duckdb
from pydantic import BaseModel
from edgar.agent.memo import Memo, Claim
from edgar.tools.compute import recompute, ComputeError

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
            f"SELECT filed_date FROM {table} WHERE {col} = ?", [cid]).fetchone()
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
                section=section, claim_text=claim.text, rule="needs_citation",
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
```

One detail the derivation check exposes: `_visible` distinguishes `derivation_recomputes` from `citation_resolves` by matching `"recompute"` in the problem string — the stored-value-mismatch message must therefore contain the word `recomputes` (it does: "but recomputes to"). Keep those message strings stable or split `_visible` into two functions.

- [ ] **Step 4: Run** — both test files, then full suite → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/edgar/agent/memo.py src/edgar/agent/guardrails.py \
  tests/test_memo.py tests/test_guardrails.py
git commit -m "feat(agent): structured memo + deterministic reply-path guardrails

Rejections downgrade to labeled hypotheses, never delete (spec 7.3);
rejection_count is itself a reported metric.

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

