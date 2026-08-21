# SDD ledger — plan: docs/superpowers/plans/2026-08-19-stage2-diligence-agent.md

Branch: feat/stage2-diligence-agent. Spec: docs/superpowers/specs/2026-08-18-cited-diligence-agent-design.md (rev 3, binding authority).

## Pre-flight conflict scan (2026-08-19)

Pairs sharing files/interfaces:
| Producer | Consumer | Checked | Found |
|---|---|---|---|
| T1 CANONICAL_FIELDS (15, exact names) | T3 validate, T10 rubrics, T14 working_capital exprs | names match verbatim (inventory, accounts_receivable, accounts_payable, long_term_debt, cash_and_equivalents) | clean |
| T3 schemas (Computation w/ as_of, SpanDTO w/o doc_id) | T4 compute return, T7 search select-list | field lists match | clean |
| T4 derivation DDL (derivation_id, expression, inputs_json, value, as_of) | T12 guardrails _visible SELECT as_of,value; T15 recompute | columns match | clean |
| T6 span DDL FLOAT[384] | T7 EMBED_DIM=384, T15 test 11-col insert | dims + arity match | clean |
| T7 search_spans(embedder=, items=) | T14 dispatch_tool call | kwargs match | clean |
| T8 save_session/record_conclusions kwargs | T14 emit; T15 temporal reads session.recalled_conclusion_ids | match | clean |
| T9 tracer.span/event | T14 nodes usage | match | clean |
| T10 SECTIONS slugs | T14 nodes import + _CONCLUSION_SECTIONS ⊂ slugs | match | clean |
| T11 LedgerEntry mutable dataclass, positional (kind,identifier,gist,section,payload) | T14 dispatch sets e.section post-hoc | mutable + order match | clean |
| T12 Memo (mutable BaseModel) | T14 parse_structured(Memo) + model_copy(update) | compatible | clean |
| T13 LLMClient kwargs | T14/T15 call sites | match | clean |
| T16 compute_metrics(memo_meta keys) | EvalReport required fields | keys cover fields | clean |

Per-task self-consistency: each task's tests exercise only interfaces its own Files/Interfaces block defines; T3 test helper amendment (init_schema) is called out in the task text; T14 graph test scripts 22 turns for 11 sections (2/section) — matches plan_node output; T15 scale-matching arithmetic verified by hand (2.1e9 vs 2.11e9 = 0.47% ≤ 2%; kappa examples verified).

Rulings (pre-flight):
- Ruling: execute in the existing checkout on feat/stage2-diligence-agent, no new worktree — .env (gitignored), venv, and the real DuckDB path live here; branch is not main. Cost if wrong: dirty-tree interference; mitigated by clean `git status` at start.
- Ruling: commit trailer is `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>` (harness norm) overriding the plan's shorter form. Cost if wrong: cosmetic.
- Ruling: existing tests asserting a count of 10 canonical fields (if any beyond test_mapping) may be updated to 15 by the T1 implementer — count-tracking assertions, not behavior changes. Cost if wrong: masks a real regression; low.
- Ruling: manual checkpoints requiring credentials the repo does not hold (ANTHROPIC_API_KEY for T14/T16 step 5; LANGFUSE keys + docker for T9 step 5) are ledgered as pending-human, not run by subagents. Checkpoints needing only network/disk (T1 rebuild, T6 fetch, T7 index) ARE run. Cost if wrong: none — pending items surfaced at finish.

## Task log
Task 1: implemented (commit 06ae7df, 142 tests green, rebuild verified: inv 3,653 / AR 5,231 / AP 5,681 / LTD 3,686 / cash 9,638 ciks). Two machine-sleep interruptions mid-task; caffeinate -t 10800 started as mitigation. Review dispatched.
Task 1: review clean (spec ✅, Approved). Minor (deferred): report prose mis-groups MR id ranges (code correct); idempotency test doesn't cover removed-rule staleness scenario (inherited from brief).
Task 1: Ruling: controller detected the real-store rebuild was interrupted mid-insert by machine sleep (fact table at 876,546 rows vs 4.72M baseline; implementer's Step-5 verification numbers were measured on partial data). Controller re-ran `make rebuild-curated` to completion as verification of task evidence, not a code fix — no code changed. Cost if wrong: none; rebuild is idempotent. Correct coverage numbers to be re-captured on completion.
Task 1: complete (commits 4e15ab3..06ae7df, review clean; 2 minors deferred; store rebuild re-run pending verification)
Task 2: complete (commits 06ae7df..38953a1, review clean; 146 tests). Minor (deferred): test_load_secrets_env_sets_and_reports leaks ANTHROPIC_API_KEY=sk-test into os.environ for the rest of the pytest process (brief-mandated test shape); .env values not quote-stripped in load_secrets_env. Both inherited from brief; final review to triage.
Task 3: Ruling: machine sleeps within seconds of each wake window; 4 consecutive implementer resumes died before writing any file. Controller transcribes Task 3 directly from the brief (code fully specified in plan); task review will still be dispatched before completion. Cost if wrong: controller context pollution; accepted under environmental failure. Sub-agent dispatching resumes when the machine is stable.
Task 3: implemented by controller per ruling (commit 157d552, 150 tests green). Brief defect found+fixed: test _db helper also needs create_mapping_table (coverage_map reads mapping_rule). Review deferred until machine is stable — queued.
Task 4: implemented by controller per Task 3 ruling (156 tests green; spec §6 amended rev 3a). Review queued.
Task 5: implemented by controller per Task 3 ruling (commit a3820d2, 159 tests green). Review queued.
Task 6: code implemented by controller per Task 3 ruling (162 tests green). Ruling: adopted plan's own fallback — real fetch vendored into scripts/fetch_narratives.py (edgartools-only, sys.path-stripped); library edgartools_fetcher raises by design. Cost if wrong: none — tested surface unchanged. Real pull queued for stable window. Review queued.
Task 7: code implemented by controller per Task 3 ruling (commit follows c6cffcb; 168 tests green). Real-store indexing queued. Review queued.
Task 8: implemented by controller per Task 3 ruling (171 tests green). Review queued.
Task 9: code implemented by controller per Task 3 ruling (174 tests green). Langfuse backend setup pending-human. Review queued.
Task 10: implemented by controller per Task 3 ruling (179 tests green). Ruling: system.md amended to contain literal `as_of` (plan test vs plan prompt-text inconsistency; content amended, test kept). Review queued.
Task 11: implemented by controller per Task 3 ruling (commit b53a1ad, 183 tests green). Review queued.
Task 12: implemented by controller per Task 3 ruling (190 tests green). Review queued.
Task 13: implemented by controller per Task 3 ruling (commit 29cb9bf, 193 tests green). Review queued.
Task 14: code implemented by controller per Task 3 ruling (201 tests green; offline e2e graph run passes). Real memo run pending-human (needs ANTHROPIC_API_KEY). Review queued.
Task 15: implemented by controller per Task 3 ruling (209 tests green). Review queued.
Task 16: code implemented by controller per Task 3 ruling (217 tests green). Ruling: calibration sampler consumes *.verdicts.json not *.report.json — reports hold aggregates, verdicts hold claims; plan signature adjusted accordingly. All 16 tasks now implemented. Reviews for tasks 3-16 queued; real-store/API checkpoints pending.
Ruling: tasks 3-16 were controller-transcribed under one ruling from one plan; their queued per-task reviews are consolidated into ONE reviewer dispatch over the combined diff (38953a1..dc416a9) with all briefs attached, on opus (replaces 14 review seats). The final whole-branch review still follows separately on the most capable model. Cost if wrong: a defect in one task hides in a bigger diff; mitigated by the final review pass.
Consolidated review (tasks 3-16): Needs fixes — 1 Critical (adversarial glob picks .verdicts.json), 7 Important (ledger mid-line truncation, numeric regex fires on dates/years, reliability evidence unreachable [-> add get_fact_history tool], temporal_leakage contaminated by integrity failures [-> split visibility module], repair skips hypotheses, QA mode still demands 11 sections, anthropic under-pinned). 20 Minor deferred to final review. Fix round 1 dispatched (sonnet) with rulings for all 8.
Checkpoint: narrative fetch DONE (38 filings, 114 items, verify_store clean; TSLA only 2 filings — noted). Environment bug found+fixed: edgartools' `edgar` package shadowed ours in site-packages after the [narrative] install; uninstalled post-fetch and hardened Makefile with PYTHONPATH=src. Ruling: fetch script reinstalls edgartools transiently; make targets are now shadow-proof.
Ruling: anthropic finding #8 verified locally — SDK resolved to 1.0.0, messages.parse(output_format=) present; fix is pin-raise only.
Checkpoint: index DONE (4,449 spans / 114 docs; Items 1/1A/7 = 950/1624/1875). Real-store retrieval spot-check passed: AAPL Item 7 search returns on-topic spans, all filed <= as_of, span text == doc[char_start:char_end]. Commit 50fa43b (index_spans self-init + Makefile PYTHONPATH hardening).
Remaining pending-human: langfuse backend (docker), make memo / make eval / adversarial sweep (need ANTHROPIC_API_KEY), 150-claim labeling (spec task 2.6), writeup (2.7).
Fix round 1/5 (consolidated 3-16): 8/8 addressed, 0 open, no new breakage (re-review verified; commits dc416a9..e58da82). 227 tests green.
Task 3-16: complete (commits 38953a1..50fa43b, consolidated review + fix + re-review clean; 20 minors deferred to final review — list in review-3-16-report.md).
Final whole-branch review (fable): Fix before merge — 5 must-fix (F1 real-store agent tables missing -> make memo crash, reproduced; F2 parsed_output None unguarded; F3 adversarial results lack config_version/as_of; F4 latency never captured; F5 spec docs — record §7.6 one-shot QA divergence + eligibility gate non-enforcement, fix stale total_debt in §12). 19 of 20 minors stay deferred. Cross-cutting hunt clean: no as_of leaks, identifier integrity sound, prompts cache-ordered, rev3a/long_term_debt consistent. Final fix wave dispatched (ONE fixer, sonnet).
Final fix wave: commit 1d62eac, 230 tests green (3 new). Fixer deviations (sound, accepted): run_eval ensures only derivation+memory tables (narrative already exists on real store — matches finding text); F1 regression test also creates mapping_rule (coverage_node reads it; matches production shape). Scoped re-review dispatched (haiku).
Final re-review: 5/5 ADDRESSED, no new breakage. Branch approved. 230 tests green.
Ruling: workspace preserved and committed (mirrors Stage 1 convention of keeping the SDD decision record in-repo) instead of the skill-default rm -rf. Cost if wrong: repo clutter.
