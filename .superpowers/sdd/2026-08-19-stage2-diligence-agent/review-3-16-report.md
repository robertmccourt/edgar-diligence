# Consolidated review — Tasks 3–16 (38953a1..dc416a9)

Reviewer: consolidated task reviewer. Scope: 14 commits, 69 files, +3428 lines.
Method: read the full diff, all 14 briefs, all 14 reports, and the final source
tree; targeted one-off python probes to settle behavioural questions (marked
"verified" below). The test suite was not run (217 reported green).

---

## 1. Spec compliance against the binding global constraints

| # | Constraint | Verdict | Evidence / caveat |
|---|---|---|---|
| 1 | Point-in-time enforced in SQL, never by prompt; `TOOL_DEFS` input schemas must not expose `as_of` | ✅ | Every read path filters in SQL: `query/asof.py:83` (`filed_date <= ?`), `query/coverage.py:77,98,108,123` (all four sub-queries), `tools/facts_tools.py:62` (`list_available_facts`), `tools/peers.py:11` (fact-count join), `narrative/store.py:102,115` (both embedding and BM25 legs), `memory/episodic.py:70` (`learned_as_of <= ?`), `tools/compute.py:97` (rejects inputs filed after `as_of`), `agent/guardrails.py:32-57` (`_visible`). All five `TOOL_DEFS` entries omit `as_of`; `dispatch_tool` injects it from the harness (`agent/tool_defs.py:56`), and `tests/test_agent_nodes.py:59-62` asserts the absence. One deliberate exception, correctly documented: `facts_tools.py:42-45` picks the reference `period_end` with an **unfiltered** query — that is required for `NOT_YET_FILED` to be detectable, and it leaks a status code only, never a value. Sound. |
| 2 | Model does no arithmetic; every derived number via `compute()` returning a `derivation_id`; `+/-` require like `period_type`, ratios may cross | ✅ | `tools/compute.py:54-70` implements exactly that rule; `_eval` whitelists `Expression/Constant/Name/USub/BinOp(Add,Sub,Mult,Div)` and rejects everything else (`:71`). Names must exactly match `inputs` — unused inputs rejected (`:85-88`). Spec §6 bullet updated and a §16 rev-3a entry added, as Task 4 required. Tool description carries the rule to the model (`tool_defs.py:29-32`); `prompts/system.md:15-18` states law 2. |
| 3 | Compaction never drops identifiers | ✅ *for `compact()`* / ⚠️ undercut downstream | `ledger.py:30-38` only clears `payload` and truncates `gist`; entries and identifiers are untouched, and `tests/test_ledger.py:12-19` locks it. **But** `nodes.py:111` hard-slices `ledger.render()[:context_budget_chars]`, which does drop trailing identifiers and can emit a half-identifier — see Important #2. The letter of the constraint holds; the spirit does not. |
| 4 | Models pinned (`claude-opus-5` generation, `claude-sonnet-5` judging); adapter omits `thinking` entirely; no assistant prefill; `max_tokens=16000` | ✅ | `config/versions/v1.yaml:1-2`; `agent/llm.py:39` default `max_tokens=16000`; no `thinking` key anywhere in `llm.py`; the retrieve loop always ends `messages` on a `user` turn (`nodes.py:71-85`), and `parse_structured` sends a single user message (`llm.py:66`) — no prefill. `graph.py:48` uses `cfg.generation_model`; `run_eval.py:48` and `adversarial.py:61` use `cfg.judge_model`. Verified `client.messages.parse(..., output_format=<pydantic type>)` and `ParsedMessage.parsed_output` exist in the installed `anthropic` 1.0.0. |
| 5 | All LLM access through `LLMClient`; agent/eval never import `anthropic` outside `agent/llm.py` | ✅ | `grep -rn anthropic src tests scripts` → only `agent/llm.py:40-52,68-69` (plus a prose mention in `config.py:35`). `run_eval.py` and `adversarial.py` import `AnthropicLLM` from the seam. |
| 6 | Tests never touch the real store (`~/edgar-data`); no test hits the network | ✅ | Every new test DB is `connect(tmp_path / "t.duckdb")`. No new test calls `get_settings()`. The only `httpx` in `tests/` is `test_download.py` (Stage-1, `MockTransport`). `edgartools_fetcher` deliberately raises so no test can reach SEC (`narrative/fetch.py:75-77`), and all fetch tests inject fakes. |
| 7 | Guardrail failures downgrade to labeled hypotheses, never silently delete | ✅ sections / ❌ hypotheses | `guardrails.py:97-108` sets `is_hypothesis=True` and prefixes `[UNVERIFIED — downgraded by guardrail] `, never removing a claim; `tests/test_guardrails.py:44-53` locks it. **Gap:** `repair_memo` iterates `memo.sections` only — violations recorded under section `"hypotheses"` (`guardrails.py:92`) can never be matched, so a hypothesis with a bad citation is flagged every round and never repaired. See Important #6. |
| 8 | Eval reads the RENDERED memo markdown, never the generator's internal claim typing | ✅ | `run_eval.py:22` — `decompose(llm, render_markdown(memo))`. The judge re-derives its own `claim_type`/`claimed_value` from prose (`eval/decompose.py:10-15`); `memo.sections[*].claims[*].is_hypothesis` is never read by the eval. Only run metadata (`as_of`, `session_id`, `config_version`, `guardrail_rejections`) crosses over — not grading input. |
| 9 | Temporal check covers BOTH surfaces (cited ids and recalled conclusions) | ✅ (with a contamination caveat) | `judges.py:110-117` walks every cited id through `_visible`; `:118-126` reads `session.recalled_conclusion_ids` and checks `learned_as_of > as_of`. `tests/test_judges.py:76-95` exercises both in one assertion. Caveat: non-temporal derivation-integrity failures are reported through the same channel — Important #5. |

### On the flagged `_visible` reuse in `eval/judges.py:4`

**Sound in substance, wrong in form.** The brief for Task 12 explicitly designates
`_is_numeric_claim` as "exported for the eval's reuse" and the Task 15 brief says
"reuse `guardrails._visible`", so this is sanctioned, not smuggled. There is no
self-grading risk: `_visible` is a database-visibility predicate, not claim typing.
Two real objections remain:

- The underscore makes it a private symbol that two modules now depend on, with no
  test pinning its message strings — and `check_memo` itself already depends on the
  literal substring `"recompute"` appearing in `_visible`'s return value
  (`guardrails.py:79-80`), a coupling the brief itself flagged as fragile.
- Reusing it wholesale imports the derivation-recompute check into a function named
  `temporal_leakage`, which is what produces Important #5.

Recommendation: promote to `edgar/tools/visibility.py` with two functions —
`visibility_problem(con, cid, as_of)` (dates only) and `derivation_integrity(con, cid)` —
and have `check_memo` and `temporal_leakage` each call the ones they mean. Note
also that `_is_numeric_claim`'s advertised reuse never happened; the eval does not
import it (harmless dead intent).

### On the recorded deviations

| Deviation | Verdict |
|---|---|
| Task 3: test helper needed `create_mapping_table` | ✅ Correct. `coverage_map` reads `mapping_rule` (`coverage.py:113-114`); the brief's own note anticipated `raw_num` but missed this. Helper-only change, production code untouched. |
| Task 6: real fetcher moved wholly into `scripts/fetch_narratives.py`; library fetcher raises | ✅ Correct, and the brief itself sanctioned this fallback ("this will not work as written… vendor the fetch into the script"). The script strips `/src` from `sys.path` (`:15`) and imports nothing from this package. `edgartools_fetcher` raising with an explanatory message is better than a silent stub. Two small consequences: Minor #10 (duplicated cik list) and Minor #11 (Task 7's "call indexing after fetch" silently dropped). |
| Task 10: `system.md` amended to contain literal `as_of` | ✅ Correct call. Amending the prompt rather than weakening the test is the right direction, and the amended text ("Every tool enforces `as_of` in the database") is more accurate than the brief's original. Deleting the drafted `qa.md` was also right — `nodes._rubric_for` inlines the question-mode rubric. |
| Task 16: calibration sampler reads `*.verdicts.json` not report files | ✅ Substantively correct — `EvalReport` carries aggregates only and has no claim list, so the brief's signature was simply wrong. Verified working by hand. Two loose ends in Minor #6. |

---

## 2. Issues

### 🔴 Critical

**C1. The adversarial runner cannot complete a full 30-case sweep — the memo glob
picks up the eval's own output files.**
`src/edgar/eval/adversarial.py:71-72`

```python
memo_json = sorted(Path("data/memos").glob(f"{case.cik}_{as_of}_*.json"))[-1]
report = evaluate_memo(con, judge, memo_json)      # blob["memo"] ...
```

`evaluate_memo` writes `<stem>.report.json` and `<stem>.verdicts.json` *beside* the
memo (`run_eval.py:33,35`), and both match `{cik}_{as_of}_*.json`. Verified string
ordering: `…v1+HASH.json` < `…v1+HASH.report.json` < `…v1+HASH.verdicts.json`, so
`[-1]` selects the **verdicts** file. `json.loads` then returns a *list*, and
`blob["memo"]` raises `TypeError: list indices must be integers`.

This is not hypothetical. Verified against `assets/adversarial.yaml`: every cik in
the default-`as_of` group repeats — `Counter({320193: 3, 789019: 3, 1045810: 3,
1318605: 2, 77476: 2, 200406: 2, 354950: 2, 909832: 2, 1018724: 2, 1652044: 2})`.
Case `adv-12` (cik 320193, default as_of) is the first to hit it, at which point the
sweep dies with an unhandled `TypeError` — after ~11 paid agent runs. It also fires
on any *rerun* of the suite. Nothing in `tests/test_adversarial.py` covers `main()`.

Fix: don't glob. `run_agent` returns the `Memo`, and `emit` builds the stem
deterministically — reconstruct it:

```python
memo = run_agent(...)
memo_json = Path("data/memos") / f"{memo.cik}_{as_of}_{memo.config_version}.json"
```

(Belt-and-braces: give the eval artefacts a non-`.json` sibling suffix, or write
them into a `reports/` subdirectory.)

---

### 🟠 Important

**I1. `write_memo` silently truncates the evidence ledger mid-line, and compaction
cannot prevent it.**
`src/edgar/agent/nodes.py:111` — `state["ledger"].render()[:cfg.context_budget_chars]`

`compact()` saturates: once every `payload` is `""` and every `gist` is ≤ 200 chars,
a second call frees 0 bytes (`ledger.py:30-38`). So the ledger's floor is
`~200 × n_entries` and grows without bound in `n`, while `compaction_threshold_chars`
is 45 000 — beyond ~225 entries compaction is a no-op and the ledger keeps growing.
A full 11-section run at `max_tool_turns: 12` with `get_facts` returning 8–12 facts
per call passes 250 entries easily, at which point `render()` exceeds the 60 000-char
budget and line 111 cuts it — **mid-line**, with no warning, no trace event, and no
counter. Two concrete harms: (a) identifiers at the tail vanish from the writer's
context, which is the same failure mode `compact()` exists to prevent; (b) the cut
can leave a truncated identifier that the model copies "verbatim", producing a
`citation_resolves` guardrail violation the model cannot diagnose.

Fix: truncate on line boundaries (`"\n".join(itertools.takewhile(...))`), emit a
`span.event("ledger_truncated", dropped=n)`, and make `compact()` escalate (a second
pass could drop `note`-kind entries with no identifier, or halve `_GIST_CAP`) so
truncation is a backstop rather than the primary mechanism.

**I2. The numeric-claim detector fires on any year or ISO date, so the honesty
sections get systematically downgraded.**
`src/edgar/agent/guardrails.py:10-16`

`_NUMERIC`'s `\d{3,}` alternative matches bare years. Verified:

```
True  | 'cost_of_revenue: NOT_DISCLOSED for the quarter ended 2023-03-31'
True  | 'gross_profit was NOT_DISCLOSED in fiscal 2023'
True  | 'Management cited freight costs in 2023'
False | 'revenue: NOT_YET_FILED'
```

`prompts/sections/unanswered.md` explicitly tells the model this section "needs no
citations — status codes are the content", and `reliability.md` asks for
filing-lag observations. Every such claim that names a period becomes a
`needs_citation` violation and is rewritten to
`[UNVERIFIED — downgraded by guardrail] …` with its citations stripped. That
corrupts the section the spec designates as the honesty surface, and inflates
`guardrail_rejections`, which is itself a reported metric (`metrics.py:19`) and a
`score_answer` input path. The brief did ask for a "slightly over-eager" detector,
but it also asked to exclude "status codes / dates" — the date exclusion was not
implemented.

Fix: add a negative guard before the `\d{3,}` branch — drop ISO dates
(`\d{4}-\d{2}-\d{2}`), bare four-digit years in 19xx/20xx, and `FY\d{4}` — or
exempt claims whose text contains a `FieldStatus` token.

**I3. `reliability.md` instructs a workflow the tool surface cannot produce.**
`prompts/sections/reliability.md` vs `src/edgar/query/asof.py:71-84`

The rubric says: "When the same figure … appears with MULTIPLE filed versions in your
evidence … cite BOTH fact_ids." But `get_facts_asof` partitions by
`(canonical_field, period_start, period_end, period_type, unit)` and returns `rn = 1`
only — the superseded row is never returned. `restatement_history` exists
(`asof.py:92`) but is **not** in `TOOL_DEFS`, and no other tool surfaces prior
versions. The instructed evidence is unreachable, so section 10 can only ever emit a
status note or invent a restatement. This is a genuine cross-module mismatch between
Task 10 (prompts) and Tasks 3/14 (tools).

Fix (either): add a sixth tool `get_restatement_history(cik, field, period_end,
period_start, period_type, unit)` wrapping the existing function (`as_of` injected by
the harness), or rewrite `reliability.md` to scope itself to filing-lag evidence and
the `AMBIGUOUS` status code, and say plainly that version history is out of scope
for v1.

**I4. `temporal_leakage` reports non-temporal problems, contaminating a MUST-be-zero
gate.**
`src/edgar/eval/judges.py:115-117`

```python
problem = _visible(con, cid, as_of)
if problem is not None and "unknown" not in problem:
    problems.append(problem)
```

`_visible` returns four kinds of string. Two are temporal ("filed … after as_of",
"computed for later as_of"); two are integrity failures ("derivation … does not
recompute", "stored X but recomputes to Y"). Both of the latter land in
`temporal_problems`, which becomes `temporal_leakage_count` — the metric the spec
calls a hard zero gate (`metrics.py:17` comment, `to_markdown`'s "MUST be zero"
heading) — and is summed into the FABRICATED verdict in `adversarial.score_answer:40-42`.
A broken derivation would be reported to the reader as a point-in-time leak.

Secondary: `_visible`'s derivation branch calls `recompute`, which does
`INSERT OR REPLACE INTO derivation` (`compute.py:124`), so the eval **writes** to the
store while evaluating. Idempotent in practice, but surprising for a read path.

Fix: split `_visible` per the recommendation above; route integrity failures into a
separate `derivation_integrity_problems` field on `EvalReport`.

**I5. `repair_memo` never repairs `memo.hypotheses`.**
`src/edgar/agent/guardrails.py:100-108`

`check_memo` records hypothesis violations with `section="hypotheses"`
(`guardrails.py:92`), but `repair_memo` only walks `fixed.sections`, whose slugs are
section names — `("hypotheses", text)` can never match. Consequence: a hypothesis
citing an unknown or post-`as_of` id is flagged on every guardrail pass, burns all
`max_repair_rounds` (2), and the dangling citation ships in the rendered memo, where
the eval will then count it as leakage or as an unsupported claim. The loop still
terminates (`graph.py:28-32`), so this is a correctness gap, not a hang.

Fix: add a `for c in fixed.hypotheses:` arm keyed on `("hypotheses", c.text)` that
strips the offending citations (leaving the claim text intact — it is already labeled
speculation, so no downgrade is needed).

**I6. Question mode plans one `qa` step but the memo writer is still told to produce
all 11 sections — and `score_answer`'s REFUSED depends on that.**
`src/edgar/agent/nodes.py:42-44` vs `:101-111`, `src/edgar/eval/adversarial.py:37-43`

`plan_node` sets `plan = ["qa"]` when a question is present, and `_rubric_for` inlines
a question-specific rubric — but `write_memo` unconditionally emits the 11-section
list from `SECTIONS` and never mentions the question. So the QA answer has no
designated home, and the model is nudged to fabricate an 11-section memo from a
single section's worth of evidence. `score_answer` returns `REFUSED` only when *zero*
sections have `status == "content"`; the more sections the writer is asked for, the
less reachable that verdict is. The adversarial suite's headline `refusal_rate` runs
entirely through this path, so the metric is measuring the prompt shape as much as the
agent's honesty. (The brief has the same shape, so this is a plan-level gap the
transcription faithfully reproduced — but it is worth fixing before the paid sweep.)

Fix: in `write_memo`, branch on `state["question"]` — emit a one-section spec
(`slug="qa"`, the question text, and the same status_code instruction) instead of the
11-section list.

**I7. `anthropic>=0.125` under-pins a dependency whose 1.x API the adapter requires.**
`pyproject.toml:11`

`parse_structured` calls `client.messages.parse(..., output_format=<pydantic type>)`
and reads `.parsed_output` (`llm.py:63-71`). Verified present in the installed 1.0.0;
neither exists in the 0.12x line the constraint admits. A fresh resolve on another
machine can install a version where the memo writer and every LLM judge fail at
runtime with `AttributeError`, well after the tests pass (nothing in the suite
touches `AnthropicLLM`).

Fix: `"anthropic>=1.0,<2"`.

---

### 🟡 Minor

1. **Dead parameter.** `judges.judge_claim(..., as_of, ...)` (`eval/judges.py:34`) never
   uses `as_of`; visibility is checked separately by `temporal_leakage`. Intentional
   separation, but the parameter should go or be documented as reserved.
2. **Dead code.** `run_eval.py:20-21` — `memo.as_of if isinstance(memo.as_of, date) else …`;
   `Memo.model_validate` always coerces to `date`, so the `else` branch is unreachable.
3. **Dead state key.** `graph.run_agent` seeds `conclusions=[]` (`graph.py:59`); no node
   reads or writes it (`emit` builds its own local list).
4. **Inaccurate docstring.** `calibration.py:16` — "Deterministic: seeded by sorted input
   paths, not wall clock". It is seeded by the constant `0` (`:22`). Deterministic, yes;
   not by paths.
5. **Imports mid-file.** `narrative/store.py:22-26` places `import hashlib` / `from … import`
   *after* the DDL block and `create_narrative_tables` — an artefact of Task 7 appending to
   Task 6's file (PEP 8 E402). No linter is configured, so this is style only, but it makes
   the module read as two files stapled together.
6. **Calibration sampler: untested, and the runbook command is now wrong.**
   `sample_for_labeling` has no test at all (`test_calibration.py` covers only
   `cohens_kappa`). I verified it by hand — round-robin stratification, deterministic,
   terminates, and returns fewer than `n` when the pool is small. Two loose ends: the brief's
   default `n = 150` was dropped (now a required positional), and the plan's Step-5
   invocation still globs `Path('data/memos').glob('*report.json')`, which under the new
   contract iterates a *dict*'s keys and raises `TypeError` on `v["claim"]`. Update the
   documented command to `*.verdicts.json`.
7. **Control-flow assertion.** `narrative/store.py:93` — `assert embedder is not None`
   disappears under `python -O`, after which the function fails with a confusing
   `AttributeError`. Make the parameter required (the brief's interface had it non-optional)
   or raise `ValueError`.
8. **Unhandled `None` from the LLM seam.** `ParsedMessage.parsed_output` is `Optional`;
   `llm.py:71` returns it unguarded, so a response with no parsed block surfaces downstream
   as `AttributeError: 'NoneType' object has no attribute 'model_copy'` in `write_memo`.
   Raise `LLMError("structured output missing")` instead.
9. **Over-broad exception net.** `tool_defs.py:102` catches `ValueError`, and pydantic's
   `ValidationError` *is* a `ValueError` — so a genuine DTO/schema bug is silently reported
   to the model as a tool error string rather than failing loudly. Narrow to
   `(ComputeError, KeyError, TypeError)` plus an explicit `ValueError` raise from
   `_validate_fields`/`get_peer_set`, or log before swallowing.
10. **Duplicated constant.** `scripts/fetch_narratives.py:17-18` hardcodes `NARRATIVE_CIKS`
    rather than reading `Settings.narrative_ciks` — unavoidable given the `sys.path` strip,
    and verified identical today, but a silent drift risk. Add a comment pointing at
    `config.py` as the source of truth, and consider asserting the two match in a test that
    parses the script text.
11. **Undocumented deviation.** Task 7's brief lists `scripts/fetch_narratives.py` as
    "Modify (call indexing after fetch)". It does not — architecturally it cannot, since the
    script removes `/src` from `sys.path` precisely so `import edgar` resolves to edgartools.
    `make index` covers the gap, so the outcome is right, but the Task 7 report records no
    deviation. Worth a line in the report.
12. **Schema asks the model for fields it will never keep.** `write_memo` requests the full
    `Memo` (`nodes.py:113`) including `cik`, `company_name`, `as_of`, all three of which are
    overwritten two lines later. The model must invent an `as_of` date to satisfy the schema;
    a malformed one is a validation failure for no benefit. A `MemoDraft(sections, hypotheses)`
    model would be strictly better.
13. **Eager model download.** `graph.run_agent:50-51` always constructs
    `SentenceTransformerEmbedder()` (~90 MB one-time download, then load) even for a run that
    never calls `search_filings`. The brief said "only when a search is possible". Wrap in a
    lazy proxy.
14. **Repair keys on `(section, claim_text)`.** `guardrails.py:100` — two claims with identical
    text in one section are downgraded together, and one repaired claim's new text can never
    re-match. Benign in practice; keying on index or object identity would be exact.
15. **Silent turn-limit exhaustion.** `nodes.retrieve_section:61-85` — if the model is still
    calling tools on iteration `max_tool_turns`, the loop exits without appending the section
    note and without a trace event. Emit `span.event("tool_turns_exhausted", slug=slug)`.
16. **Fragile `as_of` extraction.** `adversarial.py:65-67` — `question.split("As of ", 1)[1][:10]`
    fed straight to `date.fromisoformat` with no `try`. Verified: all 7 `post_asof_figure` cases
    parse correctly today (`adv-24…adv-30`), so this is latent — but a future case phrased
    "As of the end of 2023" kills the whole paid sweep. Wrap in `try/except ValueError` and
    fall back to the default with a printed warning; better, add an optional `as_of` field to
    `AdversarialCase` and stop parsing prose.
17. **Vacuous assertion.** `tests/test_memo.py:23` — `assert "## 2. Growth" not in md`. The
    renderer never emits numbered headings under any input, so this can only pass. Harmless,
    inherited verbatim from the brief.
18. **Incomplete `.PHONY`.** `Makefile:1` declares only `install test build clean
    rebuild-curated`; the six targets added by Tasks 6/7/9/14/16 (`narrative`, `index`,
    `langfuse-up`, `memo`, `eval`, `adversarial`) are missing.
19. **CWD-coupled tests.** `test_procedural.py` and `test_agent_config.py` rely on the default
    `Path("prompts")` / `Path(".")` roots, i.e. on pytest being invoked from the repo root.
    True today via `testpaths`, but `load_agent_config(root=…)` already exists — use it.
20. **Positional INSERT.** `compute.py:124` — `INSERT OR REPLACE INTO derivation VALUES (?,?,?,?,?)`
    with no column list; likewise `episodic.py:59`. Column-order-coupled. `save_session:41-43`
    does it correctly with an explicit list; make the other two match.

---

## 3. ⚠️ Cannot verify from diff

- **Every manual/network checkpoint is still pending** and each is a step the tests cannot
  substitute for: `make narrative` (Task 6 step 5 — the real SEC pull plus the exhaustive
  `verify_store` enumeration over ~120 documents), `make index` (Task 7 step 5), Langfuse up
  with real keys (Task 9 step 5), the first real memo (Task 14 step 5), and the first real
  eval plus calibration sample (Task 16 step 5). The reports correctly flag these as
  pending-human; nothing in this review substitutes for them.
- **`AnthropicLLM` against the live API.** I confirmed the SDK surface (`messages.parse`,
  `output_format`, `parsed_output`, no `thinking` argument sent) against the installed
  `anthropic` 1.0.0, but never called the API. Unverified: that `claude-opus-5` /
  `claude-sonnet-5` are valid ids in this account, that structured output reliably fills
  `Memo`'s nested 11-section schema, and that `max_tokens=16000` suffices for a full memo —
  a truncated structured response is the most likely first real-run failure.
- **`LangfuseTracer` against a live v4 backend.** `start_as_current_span` / `update(metadata=…)`
  / `flush` are called on faith; `tests/test_tracing.py` exercises only Noop and Recording.
  The `make_tracer` fallback (`tracing.py:90-91`) means a drift degrades to no tracing rather
  than a crash, which is the right design.
- **`try_load_fts` / BM25 leg.** Task 7's report says FTS loaded on this machine, so the
  hybrid path was exercised — but which of the two rankings actually drove the assertions in
  `test_narrative_search.py` is not determinable from the diff, and on a machine without the
  extension the tests would still pass via the embedding-only path.
- **Retrieval quality end to end.** `FakeEmbedder` is bag-of-token-hash; it validates ranking
  plumbing and the `as_of` filter, not whether MiniLM retrieval actually surfaces the right
  MD&A passages.
- **Adversarial refusal / fabrication rates.** Never run — and per C1, cannot currently be run
  to completion.
- **Real-store performance.** Probed rather than assumed: `coverage_map` = 0.38 s and
  `list_available_facts` (24 periods) = 3.42 s against the 5.5 GB `~/edgar-data/edgar.duckdb`
  (read-only, cik 320193). Acceptable; the `raw_num` join inside `coverage_map` is not the
  bottleneck I expected it to be.

---

## 4. Verdict

**Needs fixes.**

Counts: **1 Critical, 7 Important, 20 Minor.**

The core of this work is in good shape. The point-in-time guarantee is genuinely
structural — enforced in SQL on every one of nine read paths, with `as_of` absent from
every tool schema and injected by the harness — and the same rigour shows in the
content-addressed derivation records, the identifier-preserving ledger, and an eval that
re-derives claim typing from rendered markdown rather than inheriting it. The transcription
from plan to code is faithful, and the four recorded deviations were all judged correctly;
two of them (Task 6's script isolation, Task 10's prompt amendment) chose the harder,
more honest option over the expedient one.

What blocks approval is one shipping defect and a cluster of seam problems. C1 makes the
adversarial suite — one of the deliverable's headline results — fail partway through a run
that costs real money, and it fails late, after roughly eleven paid agent invocations.
Of the Important items, I2 and I3 are the ones I would fix before any paid run: I2 has the
guardrail systematically rewriting the memo's honesty section into `[UNVERIFIED]` noise
whenever it names a period, and I3 has a rubric asking the model for evidence no tool can
return, which is an invitation to fabricate in exactly the section meant to establish
trustworthiness. I1, I4 and I5 are each a case of a mechanism that is correct in isolation
being undermined at the seam with its neighbour, and all three have small, local fixes.
None of this requires rework of the design.

---

## Fix report

All 8 findings (1 Critical + 7 Important) resolved per controller rulings. The 20 Minor
findings were deliberately left untouched, as instructed. Full suite: 217 → 227 passed
(10 new tests, 0 removed, 0 skipped).

### 1 (C1). Adversarial glob → exact path reconstruction

`src/edgar/eval/adversarial.py`: `main()` no longer globs `data/memos/{cik}_{as_of}_*.json`
(which also matched the eval's own `*.report.json` / `*.verdicts.json` siblings and could
select the wrong file via string-sort `[-1]`). It now reconstructs the exact stem
`emit()` writes: `Path("data/memos") / f"{memo.cik}_{memo.as_of}_{memo.config_version}.json"`,
using the `Memo` object `run_agent` returns rather than re-deriving `as_of`/`config_version`
from anywhere else. Also removed a stray mid-function `from pathlib import Path` (already
imported at module scope).

Test: `tests/test_adversarial.py::test_memo_json_path_matches_what_emit_writes` — builds a
`Memo`, calls `nodes.emit` with a tmp `out_dir`, and asserts the file exists at exactly
`out_dir / f"{memo.cik}_{memo.as_of}_{memo.config_version}.json"`, i.e. the same construction
`adversarial.main()` now uses.

```
$ venv/bin/pytest tests/test_adversarial.py::test_memo_json_path_matches_what_emit_writes -v
tests/test_adversarial.py::test_memo_json_path_matches_what_emit_writes PASSED [100%]
============================== 1 passed in 0.31s ===============================
```

### 2 (I1). Ledger truncation on whole lines, with an omitted-count marker

`src/edgar/agent/nodes.py`: added `_truncate_ledger(rendered, budget)`, called from
`write_memo` instead of the raw `render()[:cfg.context_budget_chars]` slice. It keeps only
complete lines that fit within `budget` and, whenever any line is dropped, appends a final
line `[{n} ledger lines omitted — over context budget]`. No more half-identifier fragments
reaching the memo writer.

Test: `tests/test_agent_nodes.py::test_write_memo_truncates_ledger_on_whole_lines` — builds
a ledger of 10 fact entries, sets `context_budget_chars` to fit exactly one whole line, calls
`write_memo`, and asserts the captured `FakeLLM` prompt contains the complete first line
verbatim, does **not** contain `[f0001]` (or any partial `[f000…]` fragment), and contains
`"9 ledger lines omitted — over context budget"`.

```
$ venv/bin/pytest tests/test_agent_nodes.py::test_write_memo_truncates_ledger_on_whole_lines -v
tests/test_agent_nodes.py::test_write_memo_truncates_ledger_on_whole_lines PASSED [100%]
============================== 1 passed in 0.08s ===============================
```

### 3 (I2). Numeric-claim detector no longer fires on bare years / ISO dates

`src/edgar/agent/guardrails.py`: split `_NUMERIC` into `_MONEY_PCT` (money/percent/bps/
magnitude words — always numeric) and a post-filter path for everything else: `_is_numeric_claim`
first checks `_MONEY_PCT`; if that misses, it strips ISO dates (`\d{4}-\d{2}-\d{2}`) and bare
19xx/20xx years from the text, then checks for any remaining 3+ digit run. This exempts dates
and years while still catching magnitudes like `"headcount of 154000"`.

Test: `tests/test_guardrails.py::test_numeric_claim_detector` extended with the four
controller-specified cases: `"Filed on 2023-05-01"` → False, `"Fiscal 2023 results"` → False,
`"Revenue was $2.1B"` → True, `"headcount of 154000"` → True (plus the original three cases,
still passing).

```
$ venv/bin/pytest tests/test_guardrails.py::test_numeric_claim_detector -v
tests/test_guardrails.py::test_numeric_claim_detector PASSED             [100%]
============================== 1 passed in 0.03s ===============================
```

### 4 (I3). Sixth tool `get_fact_history` makes the reliability rubric's evidence reachable

- `src/edgar/tools/facts_tools.py`: new `get_fact_history(con, cik, field, period_end, as_of,
  *, period_start, period_type, unit) -> list[FactDTO]`, wrapping
  `edgar.query.asof.restatement_history` and filtering to `filed_date <= as_of` (the wrapped
  function returns every version regardless of filing date; the point-in-time cap is applied
  here, mandatorily).
- `src/edgar/agent/tool_defs.py`: added the `get_fact_history` entry to `TOOL_DEFS` (schema:
  `cik`, `canonical_field`, `period_end`, `period_start` (nullable), `period_type`, `unit` —
  no `as_of`), and a dispatcher branch returning the `FactDTO` list as JSON plus one
  `LedgerEntry(kind="fact")` per version.
- `prompts/sections/reliability.md`: rewritten to name `get_fact_history` explicitly instead
  of describing a workflow no tool could produce.

Tests:
- `tests/test_facts_tools.py::test_get_fact_history_caps_to_as_of` — two versions of one
  figure, one filed after `as_of`; asserts only the visible one comes back at the tighter
  `as_of`, both at the looser one.
- `tests/test_agent_nodes.py::test_dispatch_get_fact_history_filters_and_builds_ledger` —
  dispatcher-level check that the future-filed version is excluded from both the JSON payload
  and the ledger entries.
- `tests/test_agent_nodes.py::test_tool_defs_exclude_as_of_everywhere` (pre-existing, still
  green) — now covers all 6 tools, confirming `as_of` is absent everywhere including the new
  one.

```
$ venv/bin/pytest tests/test_facts_tools.py::test_get_fact_history_caps_to_as_of \
    tests/test_agent_nodes.py::test_dispatch_get_fact_history_filters_and_builds_ledger \
    tests/test_agent_nodes.py::test_tool_defs_exclude_as_of_everywhere -v
tests/test_facts_tools.py::test_get_fact_history_caps_to_as_of PASSED    [ 33%]
tests/test_agent_nodes.py::test_dispatch_get_fact_history_filters_and_builds_ledger PASSED [ 66%]
tests/test_agent_nodes.py::test_tool_defs_exclude_as_of_everywhere PASSED [100%]
============================== 3 passed in 0.32s ===============================
```

### 5 (I4). `temporal_leakage` no longer contaminated by derivation-integrity failures

New module `src/edgar/tools/visibility.py` with two functions:
- `visible_asof(con, cid, as_of) -> str | None` — date-only: fact/span `filed_date`,
  derivation `as_of` column, unknown-id message. Never recomputes.
- `check_integrity(con, cid) -> str | None` — derivation-recompute check only; `None` for
  fact/span ids and for an unknown derivation id.

`src/edgar/agent/guardrails.py::_visible` now composes both (`visible_asof` first, then
`check_integrity` for `D-` ids), preserving `check_memo`'s existing rule dispatch (the
`"recompute"` substring check) and all prior guardrail test behaviour/messages.

`src/edgar/eval/judges.py::temporal_leakage` now imports and calls `visible_asof` directly
from `edgar.tools.visibility` — the private `edgar.agent.guardrails._visible` cross-module
import is gone, and the eval's temporal gate can no longer report a broken derivation as a
point-in-time leak.

Tests:
- `tests/test_judges.py::test_temporal_leakage_excludes_derivation_integrity_failures` — same
  tamper as `test_guardrails.py::test_derivation_recompute_checked` (UPDATE a derivation's
  stored value); asserts it appears in guardrail violations (pre-existing test) but produces
  zero `temporal_leakage` problems (new test).
- `tests/test_visibility.py` (new file, 3 tests) — direct coverage of `visible_asof` (fact,
  span, derivation, unknown id, and confirms it does NOT notice a tampered derivation value)
  and `check_integrity` (clean, tampered, non-derivation id, unknown derivation id).

```
$ venv/bin/pytest tests/test_judges.py::test_temporal_leakage_excludes_derivation_integrity_failures \
    tests/test_visibility.py -v
tests/test_judges.py::test_temporal_leakage_excludes_derivation_integrity_failures PASSED [ 25%]
tests/test_visibility.py::test_visible_asof_fact_span_and_unknown PASSED [ 50%]
tests/test_visibility.py::test_visible_asof_derivation_is_date_only_no_recompute PASSED [ 75%]
tests/test_visibility.py::test_check_integrity_derivation_only PASSED    [100%]
============================== 4 passed in 0.29s ===============================
```

### 6 (I5). `repair_memo` now repairs `memo.hypotheses`

`src/edgar/agent/guardrails.py::repair_memo` gained a second loop over `fixed.hypotheses`,
keyed on `("hypotheses", c.text)` (matching how `check_memo` records hypothesis violations).
A violating hypothesis gets its citations stripped and its text prefixed with
`"[UNVERIFIED — downgraded by guardrail] "`, same as a section claim — but `is_hypothesis` is
left alone (it's already `True`; no re-flagging needed).

Test: `tests/test_guardrails.py::test_repair_downgrades_hypothesis_citing_ghost_id` — a
hypothesis citing an unknown id (`"ghost"`) is flagged by `check_memo`, then `repair_memo`
strips its citations and prefixes its text, while `is_hypothesis` stays `True`.

```
$ venv/bin/pytest tests/test_guardrails.py::test_repair_downgrades_hypothesis_citing_ghost_id -v
tests/test_guardrails.py::test_repair_downgrades_hypothesis_citing_ghost_id PASSED [100%]
============================== 1 passed in 0.25s ===============================
```

### 7 (I6). QA mode writes a single `qa` section instead of the 11-section list

`src/edgar/agent/nodes.py::write_memo` now branches on `state["question"]`: when set, the
prompt's section spec becomes the single line `"1. qa: Question and answer"` instead of the
full `SECTIONS` listing; when unset, behaviour is unchanged. Status-code refusal remains
available in both modes (the rule sentence in the prompt body was untouched).

Test: `tests/test_agent_nodes.py::test_write_memo_qa_mode_single_section` — `FakeLLM`
captures the prompt in QA mode; asserts it contains `"qa"` and does not contain
`"working_capital"`.

```
$ venv/bin/pytest tests/test_agent_nodes.py::test_write_memo_qa_mode_single_section -v
tests/test_agent_nodes.py::test_write_memo_qa_mode_single_section PASSED [100%]
============================== 1 passed in 0.05s ===============================
```

### 8 (I7). `anthropic` pin raised

`pyproject.toml`: `"anthropic>=0.125"` → `"anthropic>=1.0,<2"`. No adapter code change —
`agent/llm.py` already targets the 1.x `messages.parse(..., output_format=...)` /
`.parsed_output` surface, which is what's installed (`anthropic==1.0.0`, confirmed via
`venv/bin/pip show anthropic`).

### Full suite

```
$ venv/bin/pytest -q
........................................................................ [ 31%]
........................................................................ [ 63%]
........................................................................ [ 95%]
...........                                                              [100%]
227 passed in 3.69s
```

### Note on scope

`Makefile` had a pre-existing uncommitted change in the working tree at the start of this
task (adding `PYTHONPATH=src` to several `venv/bin/python -c ...` targets), unrelated to any
of the 8 findings — left untouched and unstaged per the "never touch `~/edgar-data`, don't
disturb the background `make index` job" instruction; only the files touched by findings 1–8
were staged for the commit.
