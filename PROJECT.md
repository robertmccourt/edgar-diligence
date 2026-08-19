# Governed Financial Data Platform + Cited Diligence Agent

**One line:** a point-in-time SEC fundamentals database, and an AI analyst on top of it whose every claim traces back to a filing.

---

## Why this project exists

A portfolio project targeting senior AI/data-science consulting roles — EY's AI Finance, AI & Quantitative Modelling, and Applied AI Strategist tracks. It is built to demonstrate, with working code and measured results, the specific things those roles screen for: enterprise data governance, financial data modelling, ML/GenAI engineering, and cloud/DevOps practice.

It is deliberately **not** a "chat with your documents" demo. Those exist by the hundred. The differentiator is that this one can prove what it knew and when, and can measure how often it lies.

---

## The core idea, in plain terms

Companies restate their own financial figures. A company reports revenue of $100M in May; in November the auditors find an error and it refiles at $94M.

Almost every financial database silently overwrites the old number. So looking up that quarter today returns $94M — even though in September, nobody on Earth knew that. Train or evaluate a model on that data and it is quietly seeing the future.

This is **look-ahead bias**. In quantitative finance it is a famous, expensive problem: vendors charge substantial sums for "point-in-time" databases that avoid it.

**This project builds a free one from public SEC bulk data, then puts a citing agent on top and measures how often that agent fabricates.**

---

## Status

| Stage | Scope | State |
|---|---|---|
| **Stage 1** | Point-in-time data foundation | **Code complete**, 137 tests, final review passed |
| **Stage 2** | Cited analysis agent + groundedness evaluation | Designed, not yet built |
| **Stage 3** | Memory tiers, eval gate, CI/CD, governance polish | Designed, deferred |

Stage 1 has been built end-to-end and validated against real SEC filings: **6.85M raw XBRL rows → 285,430 governed facts across 6,890 companies.**

---

## Stage 1 — what is built and working

- [x] **Bulk ingestion** from SEC DERA quarterly datasets — rate-limited to SEC's published 10 req/sec, declared User-Agent, atomic downloads that cannot leave a corrupt cache
- [x] **Fail-loud schema validation** — the loader refuses to proceed if the source file layout drifts from what was verified
- [x] **Transactional loading** with measured CSV rejection rates and rollback above a documented threshold
- [x] **Bitemporal fact table** — every fact carries both the period it describes and the date it became public; restatements are appended, never overwritten
- [x] **As-of query layer** — returns only what was knowable on a given date, enforced in SQL rather than by convention
- [x] **Canonical schema + mapping layer** — thousands of heterogeneous XBRL tags projected onto 10 canonical fields, with priority-based arbitration when tags compete, and every mapping decision logged and reproducible
- [x] **Missing-value taxonomy** — distinguishes "the company never reported this" from "our mapper missed it" from "it wasn't published yet", because conflating them publishes false claims about real companies
- [x] **Universe eligibility screen** — five documented rules with removal counts at each step
- [x] **Restatement and filing-lag measurement**
- [x] **Data quality suite** — five checks with documented thresholds and stated rationale
- [x] **Coverage measurement** — the full extraction funnel as a first-class pipeline output
- [x] **Generated data dictionary** — produced from the live schema, not hand-maintained

### Verified results, against real filings

| Metric | Value |
|---|---|
| Restatement rate | 0.98% |
| Median absolute revision | 4.78% |
| Filing lag, median / p90 | 45 / 89 days |
| Facts built | 285,430 |
| Companies | 6,890 |

The filing-lag figures landing almost exactly on the SEC's statutory deadlines is the strongest available evidence the pipeline measures reality rather than artefacts.

### The extraction funnel

```
raw XBRL rows                         6,854,864   100.0%
  - tag is not one of our concepts    5,764,440    84.1%
  = maps to a canonical field         1,090,424    15.9%
      - segment-level breakdowns        778,680
      - blank values                      2,306
  = eligible                            309,438
      - competing tags arbitrated        24,008
  = GOVERNED FACTS                      285,430     4.2%
```

94,644 distinct tags exist in the source. 19 are deliberately mapped. This is targeted extraction from a heterogeneous corpus, not data loss.

---

## Stage 2 — designed, not yet built

An agent that produces a company analysis memo where **every numeric claim resolves to a fact ID that resolves to an SEC accession number**, and which cannot see anything filed after a chosen cutoff date.

- [ ] Tool layer over the curated store — all tools enforce the as-of date in SQL
- [ ] Deterministic computation — the model is **not permitted to do arithmetic**; it requests calculations over fact IDs and receives a derivation record
- [ ] Evidence ledger — a typed record of every fact and passage retrieved, from which the memo is written
- [ ] Context compaction with an inviolable rule: compress prose, never identifiers
- [ ] Narrative retrieval over 10-K sections, filtered by publication date
- [ ] Multi-turn follow-up questions inheriting the same cutoff
- [ ] Output guardrails — no uncited numeric claim may be emitted
- [ ] **Groundedness evaluation harness** — claims typed and verified individually
- [ ] Human-calibrated judge, agreement reported as Cohen's kappa
- [ ] Adversarial set measuring refusal versus fabrication
- [ ] Per-run tracing, instrumented during the build rather than retrofitted

### Why the evaluation is the real deliverable

Financial services is blocked on deploying LLMs not by capability but by **provable groundedness** — a model that invents a number is a liability event. Building the analyst is table stakes. Measuring how often it lies, and being able to trace any output to a source document, is the part nobody has.

The harness types every claim before scoring it: numeric (checkable against the fact table), derived (recomputable from cited inputs), attributed (does the cited passage actually say that), inferential (opinion — check its premises), unsupported (no citation at all, counted as a headline metric). Plus a **temporal leakage rate** that only exists because of the bitemporal store.

---

## Stage 3 — designed, deferred

- [ ] Three-tier memory: procedural, semantic, episodic — with cross-session consolidation via a cheaper model
- [ ] Evaluation gate blocking release when groundedness regresses
- [ ] Versioned agent configuration (prompt, model, tools, retrieval parameters)
- [ ] LLM-assisted mapping of the long tail of custom tags, with full audit logging
- [ ] Governance artifact pack and model card
- [ ] CI pipeline

---

## Technology

**Built with:** Python 3.11 · DuckDB · SQL · pytest · Pydantic · httpx

**Stage 2 will add:** LangGraph · LLM tool-use / function calling · RAG with hybrid retrieval · LLM-as-a-judge evaluation · Langfuse tracing

**Stage 3 will add:** eval-gated CI/CD · versioned model configuration · drift monitoring

---

## Résumé-facing capabilities

**Data engineering & governance** — bitemporal data modelling, slowly-changing dimensions, point-in-time correctness, master data management, canonical schema design, entity/tag normalisation across a heterogeneous corpus, data lineage, data quality frameworks with documented thresholds, idempotent transactional pipelines, universe construction with documented inclusion criteria.

**Financial domain** — XBRL and US GAAP taxonomy, SEC filing structure (10-K/10-Q/20-F), income statement / balance sheet / cash flow modelling, fiscal calendar alignment, restatement analysis, look-ahead bias and survivorship bias controls, consolidated versus segment reporting.

**AI/ML engineering** — LLM agent architecture, tool-use design, retrieval-augmented generation, context and memory management, LLM-as-a-judge evaluation with human calibration, hallucination measurement, adversarial testing, model risk documentation.

**Engineering practice** — test-driven development, adversarial code review, git branching with a full decision record, dependency-ordered task planning, observability-first instrumentation.

---

## What makes this defensible in an interview

Most portfolio projects are demonstrations that worked. This one has a documented record of **nine substantive defects found in the plan**, two of which were caught only by running against real data after a green test suite of 100+ tests said everything was fine:

1. **A period-length collision.** Every quarterly report contains a three-month figure and a year-to-date figure ending on the same day. The code treated them as one quantity, making 14,148 real figures unreachable by any query.
2. **A tie-breaking column that never fired.** A `priority` field existed specifically to choose between competing tags, but the query was written so it was never consulted. A single filing emitted four conflicting values for net income.

The headline restatement statistic went **12.19% → 6.40% → 0.98%** across those fixes. The first two numbers were measuring bugs, not company behaviour.

That is the honest story, and it is a better one than "it worked first time." It is a concrete demonstration of why point-in-time discipline, adversarial review, and validation against messy reality actually matter — and of catching the failure in your own work before anyone downstream trusted it.

---

## Known limitations, documented rather than hidden

- The company dimension is a most-recent-filing snapshot, not bitemporal — a 2020 query sees a company's current industry classification
- Universe survivorship bias is documented rather than solved; delisted and acquired companies are absent
- Segment-level facts are excluded from the canonical layer entirely
- Where a parent-only and a noncontrolling-interest-inclusive figure compete, the parent-only figure is kept and the other is not stored
- No price or market data — SEC filings contain none
- The canonical schema is 10 fields; working-capital analysis would need three more

## Data source

SEC EDGAR Financial Statement Data Sets (DERA), public domain. All requests send a declared User-Agent and respect the published rate limit.
