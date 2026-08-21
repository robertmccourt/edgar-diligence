# Final whole-branch review — Stage 2 (feat/stage2-diligence-agent, 99dc356..50fa43b)

Reviewer: final whole-branch gate. Scope: 20 commits, full source tree as of 50fa43b,
spec rev 3/3a, the consolidated 3–16 review + fix wave, all `Ruling:` lines, and the
20 deferred minors. Method: read every Stage-2 source module in final form (not just
the diff), ran the full suite, and probed the REAL store read-only for the conditions
the first paid runs will meet. Suite: **227 passed in 3.8 s, fully offline.**

## Verdict

**Fix before merge — 5 items.** All five are small (two ~10-line code fixes, one
one-liner, one ~5-line addition, one docs-only pass). Nothing requires design rework.
The core guarantees — as-of in SQL on every read path, no model arithmetic,
identifier-preserving compaction, dual-surface temporal check, config-hash versioning —
survive a hostile whole-branch read intact.

Counts: **5 must-fix · 19 of 20 deferred minors stay deferred (1 promoted) · 7 new
minor observations logged, all deferrable.**

---

## Must-fix before merge

### F1 (blocker) — Real store lacks `derivation` / `session` / `session_conclusion`; nothing on the agent path creates them

Verified against `~/edgar-data/edgar.duckdb` (read-only): `fact`, `company`,
`mapping_rule`, `narrative_doc`, `span` exist; **`derivation`, `session`,
`session_conclusion` do not.** Nothing in `run_agent` initializes them — the test
suite never sees this because every test `_con()` helper calls all the `create_*`
functions explicitly. Reproduced both failure modes:

- `make memo` dies at the **first node**: `load_memory` → `recall_conclusions` →
  `duckdb.CatalogException: Table with name session_conclusion does not exist`
  (before any API spend — but a hard crash of the flagship command).
- Worse latent shape: `duckdb.CatalogException` is **not** a `ValueError`, so if the
  session tables existed but `derivation` didn't, the first `compute` call would
  escape `dispatch_tool`'s `except (ComputeError, ValueError, KeyError, TypeError)`
  net and crash **mid-paid-run** (reproduced); `guardrails._visible` and eval
  `recompute` read `derivation` too.

This is the exact bug class already fixed once for `index_spans` in 50fa43b ("real
store may predate the span table"); the same hardening was not applied here.

**Fix:** in `graph.run_agent` (after `con` is resolved) call
`create_derivation_table(con)` and `create_memory_tables(con)`; same in
`run_eval.main` (its `temporal_leakage`/`recompute` paths read `session` and
`derivation` when evaluating an older memo on a fresh store). All three DDLs are
`CREATE TABLE IF NOT EXISTS` — idempotent and test-safe. Add one test that runs
`run_agent` (FakeLLM) against a con **without** pre-created agent tables.

### F2 — `AnthropicLLM.parse_structured` returns `resp.parsed_output` unguarded (promoted Minor #8)

`ParsedMessage.parsed_output` is Optional; a truncated or refusal response returns
None, which surfaces as `AttributeError: 'NoneType' object has no attribute
'model_copy'` inside `write_memo`. The consolidated review itself named a truncated
structured response "the most likely first real-run failure" — when it happens, the
operator should see `LLMError("structured output missing (stop_reason=…)")`, not an
opaque traceback two modules away. ~5 lines in `agent/llm.py`.

### F3 — `data/adversarial_results.json` is not config-stamped

The adversarial sweep's summary artifact records case ids, traps, verdicts, and the
headline refusal/fabrication rates — with **no `config_version`** (and no per-case
`as_of`). This is the only §9.2-traceability gap found on the branch: every other
output (memo stem + JSON, session row, eval report) carries `config_version`.
Recoverable today only by cross-referencing the per-memo report files. One line in
`adversarial.main` (`"config_version": cfg.config_version` plus `as_of` per case row)
before the ~30-run paid sweep produces the artifact the writeup will quote.

### F4 — Latency per memo is never captured, and cannot be backfilled

§8.2 lists "Cost and latency per memo"; the ERD gives `MEMO.latency_s`. The memo JSON
records token usage (cost is derivable) but nothing measures wall-clock time anywhere
on the run path. Unlike every eval metric — recomputable later from stored memos —
latency not captured during the paid runs is gone. ~5 lines: `time.monotonic()`
around `build_graph().invoke(state)` in `run_agent`, stashed alongside `usage` in the
emitted JSON.

### F5 — Docs-only: record the two silent spec-vs-built divergences; fix one stale field name

The branch's recorded deviations are exemplary (see §Spec-deviations below). Two
divergences are **not** recorded anywhere, and the spec is binding authority:

1. **§7.6 / §2 multi-turn follow-ups.** The spec's flow has `REPLY → FU → RETR`
   (follow-ups inside one session, same ledger, enabling "does groundedness degrade
   over a long session?"). Built instead: one-shot question mode (`--question`) as a
   separate session, linked only through dated episodic memory. Defensible scoping —
   but it silently unreaches a spec-stated eval question and §2 lists "multi-turn
   follow-ups" as in-scope. Needs a §16 entry (delivered form + what moved to
   Stage 3).
2. **§7 flow eligibility gate (`ELIG → no → STOP`).** `run_agent` never checks
   `company.eligibility_status`; it will produce a memo for an ineligible or unknown
   cik. Harmless for the hand-picked 10-company memo set, but it is a drawn gate that
   does not exist. Either add the two-line check or record the deferral in §16.

Also: spec §12 task 2.0's row still reads "…payables, **total_debt**, cash" (line
787) while §4.4 and §16 record the `long_term_debt` rename — one stale word in the
binding doc; fix in the same pass.

---

## Mandate 1 — Spec §12 Stage 2 audit (2.0–2.5)

| Task | Verdict | Evidence |
|---|---|---|
| 2.0 15-field schema | ✅ | `CANONICAL_FIELDS` = 15 exactly; seed rules MR-0020..0030 with measured rationales (tag counts against the full store); tiered `long_term_debt` (LongTermDebt → +leases → Noncurrent); rebuild verified against the real store (inv 3,653 / AR 5,231 / AP 5,681 / LTD 3,686 / cash 9,638 ciks). `rebuild_curated` deletes-then-rebuilds so removed rules can't linger. |
| 2.1 Tool layer | ✅ | Five spec tools + `get_fact_history` (added by sanctioned fix I3). Pydantic DTOs throughout; `as_of` absent from all six input schemas (pinned by test) and injected by the harness; §4.6 statuses on every missing field; §4.8 calendar guard in `compute` (cross-company quarter + duration-length checks). |
| 2.2 Narrative store | ✅ | Offset-preserving chunks (`text[start:end]` identity), hybrid RRF (embedding + BM25 with graceful FTS fallback), `filed_date <= as_of` on both legs. Real store: 114 items / 38 filings / 4,449 spans, `verify_store` clean, retrieval spot-check passed. Known shortfall, honestly ledgered: TSLA has only 2 filings. Fetch is library-first (`edgartools`) and script-isolated because of the `edgar` package-name collision — recorded ruling, Makefile shadow-proofed. |
| 2.2b Dated episodic memory | ✅ | Plain DuckDB tables, no embeddings; `learned_as_of <= as_of` in SQL; `record_conclusions` stamps the producing session's `as_of` (docstring states the guarantee); recall blocked in `load_memory` (test: future conclusion invisible). QA runs deliberately record no conclusions (`_CONCLUSION_SECTIONS` excludes `qa`) — adversarial sweeps can't pollute memory. |
| 2.3 Agent | ✅ | LangGraph loop matching the §7 flow (minus ELIG and FU — see F5); typed evidence ledger; compaction clears payloads/truncates gists, never identifiers (test-pinned), with whole-line budget truncation + omission marker as backstop; deterministic guardrails (cited-resolves, as-of-valid, derivation-recomputes, downgrade-never-delete incl. hypotheses); bounded repair loop; CLI. |
| 2.4 Tracing | ✅ code / ⏳ backend | Tracer protocol, Langfuse v4 adapter, graceful Noop fallback; spans on sections/compaction/guardrails, `tool_call` events; `memo.trace_id` stamped. Live backend still pending-human (docker + keys) — known. Memory reads are the one §9.1 span category not traced (minor, logged). |
| 2.5 Eval harness | ✅ | Decompose-from-rendered-markdown (never the generator's typing); five-type judges (NUMERIC scale/sign-aware, DERIVED recompute, ATTRIBUTED/INFERENTIAL via judge LLM, UNSUPPORTED counted); dual-surface temporal check (cited ids + recalled conclusion ids), now uncontaminated by integrity failures (visibility split + tests); metrics + markdown report; 30-case/4-trap adversarial set with all embedded dates parsing; kappa calibration. Pulled 3.5 (adversarial) forward — a bonus vs. §12. |
| 2.6 / 2.7 | ⏳ | Human-reserved, as designed. Real memo/eval/adversarial runs need the user's API key — known pending-human, correctly ledgered. |

**Promised but absent** (beyond the known pending-human items): §7.6 in-session
follow-ups and the §7 eligibility gate (→ F5); §8.2's *section completion rate* and
*citation precision* are not computed as named metrics (both derivable later from
stored memo JSON + verdicts — deferrable), and *latency* is not captured (**not**
derivable later → F4). §15 Stage-2 success criteria are otherwise all reachable with
the artifacts as built.

## Mandate 2 — Triage of the 20 deferred minors

**Promoted: #8** (unguarded `parsed_output` → F2). **The other 19 stay deferred** —
none bites correctness or the imminent runs:

- #1, #2, #3 (dead param/branch/state-key), #4 (calibration docstring), #5 (E402),
  #11 (report prose), #14 (repair keyed on text), #17 (vacuous line amid real
  assertions), #18 (.PHONY), #19 (cwd-coupled tests), #20 (positional INSERTs):
  cosmetic/latent; no runtime path.
- #6 (calibration sampler untested; plan's Step-5 command globs `*report.json` and
  would TypeError): the **code** is correct (hand-verified by the consolidated
  reviewer; contract is `*.verdicts.json`); only the plan's documented command is
  stale. Note for the operator at labeling time; folding a corrected runbook line
  into F5's doc pass is free.
- #7 (`assert embedder`), #9 (broad ValueError net — note it also swallows pydantic
  `ValidationError`), #12 (Memo schema asks for fields it overwrites), #13 (eager
  SentenceTransformer per `run_agent` call — ~30 model loads across the adversarial
  sweep, seconds each, annoying not fatal), #15 (silent turn-limit exhaustion),
  #16 (as_of prose-parse — all 30 current cases verified parsing; do not add cases
  with non-ISO dates until fixed): real but small; none changes results.
- #10 (duplicated cik list in fetch script): verified still identical to
  `Settings.narrative_ciks` today.
- Task-2 leftovers: env-leaking test (harmless offline) and **.env quote-stripping**
  in `load_secrets_env` — checked the actual `.env`: it currently holds only
  `EDGAR_*` paths, no secrets, so nothing bites *yet*; when adding
  `ANTHROPIC_API_KEY` for the real runs, **write it unquoted** or a 401 results
  (fails before spend). Deferred with that operator note.

## Mandate 3 — Cross-cutting hunts

**as_of leaks: none found.** Enumerated every read path in final form:
`get_facts_asof` (partition + `filed_date <= ?`), `coverage_map` (all four
sub-queries, including the raw_num/raw_sub tag scan), `list_available_facts` (both
queries), `get_fact_history` (cap applied in the wrapper, documented), `get_peer_set`
(fact-count join), `search_spans` (both retrieval legs; hydration only re-reads
already-filtered ids), `compute` (input filed-date check + calendar guard),
`recompute` (validates against the derivation's stored as_of), `recall_conclusions`
(`learned_as_of <= ?`), `visible_asof`, `temporal_leakage`. The two deliberate
unfiltered reads (get_facts' reference `period_end`; coverage's `filed_later`) leak
status codes only, by design, and are documented. The `company` dimension is a
current-day snapshot (name/sic/eligibility) — a known, documented Stage-1 limitation,
not a fact leak.

**Identifier integrity: sound.** Ids are content-addressed 16-hex (verified against
the real store) — no collision with the "unknown" message heuristic. Compaction
preserves identifiers (pinned); budget truncation is now whole-line with an omission
marker (pinned, including the no-partial-`[f000` assertion). Fabricated citation ids
are correctly classified everywhere they can appear: guardrail `citation_resolves` →
downgrade-not-delete (incl. hypotheses); eval UNSUPPORTED; and **excluded** from
temporal leakage (a fabrication is not a time-travel event). One fragility logged
below (the `"unknown"` substring is unpinned). Note/coverage/peer ledger entries
render as `[]` (empty identifier) — the writer is told to cite bracketed ids; an
empty-bracket citation would be caught by guardrails as unresolvable. Minor, logged.

**Prompt-cache hostility: none — but caching is never enabled.** Ordering is
cache-correct throughout: `retrieve_section` builds `system` as stable-prompt-first +
per-run suffix, constant `TOOL_DEFS`, append-only messages within a section;
`write_memo`/judges/decompose put all varying content in the user prompt after a
constant system string. However `agent/llm.py` sets no `cache_control` breakpoints,
so on the Anthropic API **no caching occurs at all** — the ~4KB system+tools block is
re-billed on every one of up to 132 tool turns per memo. Not a correctness issue;
worth one line of `cache_control` on the tools/system before the paid sweep if budget
matters. Deferred (cost, not correctness).

**config_version stamping: one gap** — the adversarial summary (→ F3). Everything
else traced: memo stem + JSON, `session.config_version`, eval report, calibration
inputs via verdict filenames; `config_version = name + sha256(yaml + all prompt
files)[:8]` moves when prompts change (test-pinned).

## Mandate 4 — Test-suite health

227 passing in 3.8 s, fully offline, no skips. Sampled broadly: assertions are
behavioral, not incidental — as-of pins exist at every layer (tools, narrative,
memory, compute, guardrails, judges, graph). AST scan found exactly one assertion-free
test (`test_extract.py::test_validate_columns_accepts_superset` — a legitimate
does-not-raise test, Stage 1). The one vacuous assertion (Minor #17) sits among real
assertions.

- **The `"recompute"` substring dispatch is now pinned**:
  `test_guardrails.py::test_derivation_recompute_checked` tampers a stored derivation
  and asserts `rule == "derivation_recomputes"` — a rewording of
  `check_integrity`'s message flips the rule name and fails the test. Closed.
- **The parallel `"unknown"` substring in `temporal_leakage` is NOT pinned**:
  `judges.py:116` excludes fabricated ids from the MUST-be-zero gate via
  `"unknown" not in problem`, coupled to `visible_asof`'s wording across a module
  boundary, and no test feeds a fabricated id to `temporal_leakage`. Behavior is
  correct today; a future rewording would silently count fabrications as leaks in
  the headline metric. **Strongly recommended** (~8-line test), fold in with F-fixes
  if convenient; not gating since current behavior is verified correct.
- **The gap tests structurally cannot see is F1** — every test creates its own
  schema, so no test exercises "agent meets the real store's actual tables." The F1
  fix should carry the regression test noted there.

## Mandate 5 — The recorded deviations

**Rev 3a (compute type rule): cleanly recorded, consistently applied.** Spec §6
bullet + §16 entry; `_eval` enforces exactly additive-like-types/ratios-may-cross;
the tool description tells the model the rule; and every rubric that needs a
cross-type ratio (working_capital days metrics, capital_intensity asset turnover)
states it is expected and allowed. All rubric formulas re-checked against the rule —
all pass (`* 91` constants are legal; all +/− are like-typed).

**`long_term_debt` rename: cleanly recorded, consistently applied.** Zero live
`total_debt` references in code/tests/prompts; rationale with measured tag counts in
spec §4.4 + §16, mapping rule MR-0025, generated data dictionary (both tables), and
`leverage.md` — which additionally instructs the model to state the understatement
caveat and *never* say "total debt". The one stale mention is spec §12's task-2.0 row
(→ F5). The plan-level deviations (Task 3 helper, Task 6 script isolation, Task 10
prompt amendment, Task 16 verdicts-file contract) were all re-verified as correctly
judged; the fix-wave's 8 resolutions all check out in final source with real tests.

## New minor observations (deferred, for the record)

1. `temporal_leakage`'s unpinned `"unknown"` exclusion (above — recommended test).
2. No `cache_control` → prompt caching never engages (cost only).
3. Adversarial cases sharing (cik, as_of, config) overwrite each other's memo files
   on disk (scored before overwrite; per-case verdicts preserved — only post-hoc
   memo audit of duplicates is lost).
4. Empty-bracket `[]` rendering for identifier-less ledger entries.
5. §9.1 "memory reads" have no trace span.
6. `check_integrity`/eval recompute writes to `derivation` on a read path
   (idempotent INSERT OR REPLACE; surprising, harmless).
7. `get_facts` classifies an entirely-empty window as NOT_DISCLOSED for all fields —
   for a pre-IPO window with no later comparative rows either, NOT_YET_FILED would
   arguably be more honest; unreachable for the curated 10-company set.

## Bottom line

The architecture the spec promised is the architecture that was built, and the
guarantees hold under a cross-cutting read: I could not construct an as-of leak, an
identifier laundering path, or an unattributable output (save the one summary file in
F3). The five must-fix items are small and four of them exist purely to protect the
imminent paid runs — F1 being the only true blocker, and a cheap one. Fix the five,
add the F1 regression test, and this branch is ready to merge.

## Fix report

All 5 must-fix items resolved, plus the strongly-recommended (non-gating) test.
227 → 230 tests, all green, still offline.

- **F1 (blocker).** `graph.run_agent` now calls `create_derivation_table(con)`,
  `create_memory_tables(con)`, and `create_narrative_tables(con)` (all idempotent
  `CREATE TABLE IF NOT EXISTS`) right after `con` is resolved, before `state` is
  built — same hardening pattern as the 50fa43b `index_spans` fix. `run_eval.main`
  does the equivalent `create_derivation_table(con)` / `create_memory_tables(con)`
  before `evaluate_memo` (its `temporal_leakage`/`recompute` paths read `derivation`
  and `session`; narrative tables already exist in the real store so weren't
  duplicated there). New regression test
  `tests/test_agent_graph.py::test_run_agent_against_real_shaped_store_without_agent_tables`
  builds a store with only `init_schema` + `create_fact_table` + `create_mapping_table`
  (needed by `coverage_node`, already present on the real store) + a `company` row —
  no `derivation`/`session`/`session_conclusion`/narrative tables — and runs the full
  graph via `run_agent` with `FakeLLM`/`FakeEmbedder`, asserting a memo comes back.
- **F2.** `AnthropicLLM.parse_structured` (`src/edgar/agent/llm.py`) now raises
  `LLMError("structured parse returned no output (stop_reason=...)")` when
  `resp.parsed_output is None`, with a docstring explaining why (`ParsedMessage`'s
  `parsed_output` is Optional; a truncated/refused generation used to surface as an
  opaque `AttributeError` inside `write_memo`). No test against the real SDK, as
  scoped.
- **F3.** `adversarial.main()` (`src/edgar/eval/adversarial.py`) now stamps
  `"config_version": cfg.config_version` at the top level of the summary dict written
  to `data/adversarial_results.json`, and each case row carries `"as_of":
  as_of.isoformat()`. Runner `main()` is API-gated (needs `ANTHROPIC_API_KEY`); no new
  test, per the finding's own scoping — existing `test_adversarial.py` (naming
  contract, refusal/fabrication scoring) untouched and still passing.
- **F4.** `run_agent` now stashes `t0=time.monotonic()` in the initial `state` dict
  (before `build_graph().invoke(state)`). `nodes.emit` writes `"latency_s":
  round(time.monotonic() - state["t0"], 3)` into the memo JSON blob when `t0` is
  present, omitting the key when it's absent (guarded via `state.get("t0")`) so node
  tests that build state by hand and never set `t0` are unaffected. New test
  `tests/test_agent_nodes.py::test_emit_writes_latency_s_when_t0_present` sets `t0`
  and asserts the key is written and non-negative.
- **F5 (docs-only).** `docs/superpowers/specs/2026-08-18-cited-diligence-agent-design.md`:
  (a) §12 task 2.0's row no longer says "total_debt" — it names `long_term_debt` and
  points at §4.4's measured rationale; (b) appended a "rev 3b — 2026-08-20 ·
  as-built divergences recorded" entry to §16 recording that §7.6 multi-turn
  follow-ups shipped as one-shot question mode + dated episodic memory
  (in-session multi-turn deferred to Stage 3), and that `run_agent` does not enforce
  the §7 eligibility gate (deferred — the eval set is the fixed, hand-picked,
  all-eligible 10-company universe).
- **Strongly-recommended test (non-gating).** New
  `tests/test_judges.py::test_temporal_leakage_excludes_fabricated_citation_ids`
  feeds `temporal_leakage` a claim citing `"totally-fake-id"` and asserts it yields
  no temporal problem — pinning that `visible_asof`'s `"unknown identifier ..."`
  message keeps being excluded by the `"unknown" not in problem` filter, so a future
  rewording of either message can't silently start counting fabrications as leaks in
  the MUST-be-zero headline metric.

**Suite tail** (`venv/bin/pytest -q` from repo root):

```
........................................................................ [ 31%]
........................................................................ [ 62%]
........................................................................ [ 93%]
..............                                                           [100%]
230 passed in 3.91s
```
