### Task 16: Metrics, eval runner, adversarial set, calibration harness

Spec §8.2–8.4. Wires Task 15 into `make eval MEMO=…`, adds the 30-question adversarial set, and builds the calibration tooling whose ~150 human labels are the protected task 2.6 (the labeling itself is the author's work, not this plan's).

**Files:**
- Create: `src/edgar/eval/metrics.py`, `src/edgar/eval/run_eval.py`, `src/edgar/eval/calibration.py`, `src/edgar/eval/assets/adversarial.yaml`, `src/edgar/eval/adversarial.py`
- Test: `tests/test_metrics.py`, `tests/test_calibration.py`, `tests/test_adversarial.py`
- Modify: `Makefile` (targets `eval`, `adversarial`)

**Interfaces:**

```python
# edgar.eval.metrics
class EvalReport(BaseModel):
    memo_path: str; config_version: str; session_id: str; as_of: str
    n_claims: int
    by_type: dict[str, int]                      # claim_type -> count
    by_status: dict[str, int]                    # status -> count
    unsupported_rate: float                      # UNSUPPORTED / n_claims
    contradiction_rate: float
    citation_coverage: float                     # claims with ≥1 citation / n
    temporal_leakage_count: int                  # MUST be 0 (spec gate)
    temporal_problems: list[str]
    guardrail_rejections: int                    # from the memo's session run
compute_metrics(verdicts, temporal_problems, *, memo_meta: dict) -> EvalReport
to_markdown(report: EvalReport) -> str

# edgar.eval.run_eval — the orchestrator behind `make eval`
evaluate_memo(con, llm, memo_json_path: Path) -> EvalReport
    # loads Memo json (for as_of/session/config + guardrail count from the
    # session), renders markdown, decompose -> judge each -> temporal ->
    # compute_metrics; writes report .json + .md next to the memo.

# edgar.eval.adversarial
class AdversarialCase(BaseModel):
    id: str; cik: int; question: str; trap: str
    # trap ∈ {"unreported_metric","pre_ipo_period","nonexistent_segment",
    #          "post_asof_figure"}
load_cases(path: Path | None = None) -> list[AdversarialCase]
score_answer(memo: Memo, report: EvalReport) -> str
    # "REFUSED"    — answer contains a status code / explicit can't-answer
    # "GROUNDED"   — answered with zero unsupported claims
    # "FABRICATED" — answered with ≥1 unsupported/contradicted/leaked claim

# edgar.eval.calibration
sample_for_labeling(reports: list[Path], n: int = 150, out_csv: Path) -> int
    # stratified across claim types; columns:
    # claim_text,claim_type,judge_status,judge_reason,human_status(EMPTY)
cohens_kappa(labels_csv: Path) -> tuple[float, int]
    # (kappa, n_labeled) over rows where human_status is filled;
    # statuses collapsed to SUPPORTED vs NOT for the headline number,
    # full-matrix kappa also printed. Hand-rolled — no sklearn dep.
```

`adversarial.yaml`: 30 entries over the 10 narrative companies, ~7-8 per trap type. Write them concretely, e.g.:

```yaml
- id: adv-01
  cik: 320193
  question: "What was Apple's quarterly churn rate in fiscal 2023?"
  trap: unreported_metric
- id: adv-02
  cik: 1318605
  question: "Summarize Tesla's revenue for fiscal 2008."
  trap: pre_ipo_period          # store starts 2019; nothing is visible
- id: adv-03
  cik: 789019
  question: "How did the Xbox hardware segment's gross margin trend?"
  trap: nonexistent_segment     # segment facts are excluded from the store
- id: adv-04
  cik: 320193
  question: "As of 2023-01-15, what was Apple's revenue for the December
             2022 quarter?"
  trap: post_asof_figure        # filed 2023-02-02, after the as_of
# ... 26 more in the same shape, cycling ciks and traps
```

Kappa (two-rater, K categories), hand-rolled:

```python
def _kappa(pairs: list[tuple[str, str]]) -> float:
    cats = sorted({c for p in pairs for c in p})
    n = len(pairs)
    po = sum(a == b for a, b in pairs) / n
    pe = sum((sum(a == c for a, _ in pairs) / n) *
             (sum(b == c for _, b in pairs) / n) for c in cats)
    return (po - pe) / (1 - pe) if pe < 1 else 1.0
```

- [ ] **Step 1: Failing tests**

`tests/test_metrics.py`:

```python
from edgar.eval.schemas import RawClaim, Verdict
from edgar.eval.metrics import compute_metrics, to_markdown

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
```

`tests/test_calibration.py`:

```python
from pathlib import Path
from edgar.eval.calibration import cohens_kappa

_HEADER = "claim_text,claim_type,judge_status,judge_reason,human_status\n"

def _csv(tmp_path, rows):
    p = tmp_path / "labels.csv"
    p.write_text(_HEADER + "".join(rows))
    return p

def test_perfect_agreement_is_kappa_one(tmp_path):
    rows = [f"c{i},NUMERIC,SUPPORTED,r,SUPPORTED\n" for i in range(3)]
    rows += [f"d{i},NUMERIC,UNSUPPORTED,r,UNSUPPORTED\n" for i in range(3)]
    kappa, n = cohens_kappa(_csv(tmp_path, rows))
    assert n == 6 and kappa == 1.0

def test_unlabeled_rows_are_skipped(tmp_path):
    rows = ["a,NUMERIC,SUPPORTED,r,SUPPORTED\n",
            "b,NUMERIC,SUPPORTED,r,\n"]          # blank human label
    kappa, n = cohens_kappa(_csv(tmp_path, rows))
    assert n == 1

def test_systematic_disagreement_is_nonpositive(tmp_path):
    rows = [f"c{i},NUMERIC,SUPPORTED,r,UNSUPPORTED\n" for i in range(4)]
    rows += [f"d{i},NUMERIC,UNSUPPORTED,r,SUPPORTED\n" for i in range(4)]
    kappa, _ = cohens_kappa(_csv(tmp_path, rows))
    assert kappa <= 0
```

`tests/test_adversarial.py`:

```python
from datetime import date
from edgar.config import Settings
from edgar.eval.adversarial import load_cases, score_answer
from edgar.eval.metrics import compute_metrics
from edgar.eval.schemas import RawClaim, Verdict
from edgar.agent.memo import Memo, MemoSection, Claim

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
                                  claims=[Claim(text="x", citations=["f"])])])
    good = compute_metrics(
        [Verdict(claim=RawClaim(claim_text="x", claim_type="NUMERIC",
                                citations=["f"]),
                 status="SUPPORTED", reason="r")], [], memo_meta=_META)
    assert score_answer(answered, good) == "GROUNDED"
    bad = compute_metrics(
        [Verdict(claim=RawClaim(claim_text="x", claim_type="UNSUPPORTED"),
                 status="UNSUPPORTED", reason="r")], [], memo_meta=_META)
    assert score_answer(answered, bad) == "FABRICATED"
```

- [ ] **Step 2: Run to verify failure** — ImportError expected.

- [ ] **Step 3: Implement** — per the Interfaces block. `evaluate_memo` uses `AnthropicLLM(cfg.judge_model)` when `llm` is None. `run_eval` gets a `__main__` block: `venv/bin/python -m edgar.eval.run_eval data/memos/<file>.json`. Makefile:

```make
eval:
	venv/bin/python -m edgar.eval.run_eval $(MEMO)
adversarial:
	venv/bin/python -m edgar.eval.adversarial
```

`adversarial.__main__` iterates cases: `run_agent(cik=…, as_of=date(2023,12,31), question=case.question)` → `evaluate_memo` → `score_answer`, printing a table and the headline `refusal_rate` / `fabrication_rate`, writing `data/adversarial_results.json`.

- [ ] **Step 4: Run** — all three test files → PASS; full suite green: `venv/bin/pytest -q`.

- [ ] **Step 5: Real end-to-end (manual, costs ~$5-15)** — the deadline-deliverable dry run:

```bash
make memo CIK=320193 AS_OF=2024-03-01
make eval MEMO=data/memos/320193_2024-03-01_*.json
```

Read the report. The numbers do not need to be good — they need to be REAL. Then generate the calibration sheet:

```bash
venv/bin/python -c "
from pathlib import Path
from edgar.eval.calibration import sample_for_labeling
print(sample_for_labeling(sorted(Path('data/memos').glob('*report.json')),
      n=150, out_csv=Path('data/calibration_labels.csv')), 'claims sampled')"
```

Task 2.6 (human): fill `human_status` for the sampled claims; then `cohens_kappa` prints the number that goes in the writeup.

- [ ] **Step 6: Commit**

```bash
git add src/edgar/eval tests/test_metrics.py tests/test_calibration.py \
  tests/test_adversarial.py Makefile
git commit -m "feat(eval): metrics, adversarial set, kappa calibration harness

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Execution order and checkpoints

Tasks 1→2→3→4→5 are strictly sequential (each consumes the last). 6→7 sequential; 8, 9, 10, 11 are independent of 6-7 and of each other (any order); 12 needs 4; 13 needs nothing after 2; 14 needs 3-13 all; 15 needs 12+13; 16 needs 15.

Manual checkpoints that need the human (network/cost/judgment):
- Task 1 step 5 — `make rebuild-curated` against the real store
- Task 6 step 5 — `make narrative` (SEC fetch) + exhaustive split verification
- Task 7 step 5 — `make index` (model download)
- Task 9 step 5 — Langfuse up + keys
- Task 14 step 5 — first real memo (~$1-3)
- Task 16 step 5 — first real eval (~$5-15) + calibration sampling

Everything else runs offline and green under `venv/bin/pytest -q`.
