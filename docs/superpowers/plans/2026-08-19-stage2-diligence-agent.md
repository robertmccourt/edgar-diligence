# Stage 2: Cited Diligence Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A memo-writing agent over the Stage 1 bitemporal store where every claim cites a `fact_id` / `span_id` / `derivation_id`, plus the eval harness that measures how often it fabricates.

**Architecture:** Five as-of-enforced tools (facts, search, compute, peers, coverage) wrap the existing DuckDB store and a new narrative store. A LangGraph state machine drives a section-by-section retrieve→ledger→write loop; deterministic guardrails run on the reply path; a dated episodic memory (plain SQL) gives cross-session recall without temporal leakage. A separate eval pipeline (Sonnet judging Opus) decomposes rendered memos into typed claims and verifies each against the store.

**Tech Stack:** Python 3.11+, DuckDB 1.5, Pydantic v2, `anthropic` SDK (manual tool loop), LangGraph, Langfuse (behind a local Tracer protocol), edgartools (narrative fetch), sentence-transformers (local embeddings, faked in tests).

**Spec:** `docs/superpowers/specs/2026-08-18-cited-diligence-agent-design.md` (rev 3). The plan argues from the spec; read §4.4, §4.6, §6, §7, §8, §9 alongside this file.

## Global Constraints

- Python `>=3.11` (pyproject). All new code lives under `src/edgar/`; package imports are full-path (`from edgar.tools.compute import compute`) — every `__init__.py` stays empty.
- The DuckDB file lives at `get_settings().duckdb_path` (`.env` points it at `/Users/robertmccourt/edgar-data/edgar.duckdb`). Tests NEVER touch that file — every test builds a fresh DB under `tmp_path` via `edgar.db.connect(tmp_path / "t.duckdb")`.
- **Point-in-time is enforced in SQL, never by prompt.** Every tool takes `as_of: date` and filters `filed_date <= as_of` (facts, spans) or `learned_as_of <= as_of` (conclusions) inside the query.
- **The model does no arithmetic.** Every derived number goes through `edgar.tools.compute.compute()` and returns a `derivation_id`.
- **Compaction never drops identifiers.** `EvidenceLedger.compact()` preserves every identifier verbatim; there is a dedicated unit test.
- Models: generation `claude-opus-5`, judging `claude-sonnet-5` — exact IDs, no date suffixes. Omit the `thinking` parameter entirely (these models default to adaptive thinking; `budget_tokens` is removed and returns 400). Never use assistant prefill (400 on these models). `max_tokens=16000` for non-streaming calls.
- All LLM access goes through the `edgar.agent.llm.LLMClient` protocol so every agent/eval test runs offline with `FakeLLM`. No test may hit the network (SEC, PyPI models, or Anthropic).
- Dependency pins (added in Task 2): `langgraph>=1.2,<2`, `anthropic>=0.125`, `pyyaml>=6`, `langfuse>=4,<5`; optional extra `narrative`: `edgartools>=5.51`, `sentence-transformers>=6`.
- Secrets: `ANTHROPIC_API_KEY` (and optional `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_HOST`) come from the process env or `.env` via `edgar.config.load_secrets_env()`. Never commit values; never print them.
- SEC access (Task 6 only) uses `get_settings().sec_user_agent` as identity and the existing politeness conventions (≤8 req/s; edgartools handles pacing).
- TDD per task: write the failing test, watch it fail, implement, watch it pass, commit. Work on branch `feat/stage2-diligence-agent`. Run `venv/bin/pytest` (repo root); `pythonpath=["src"]` is already configured in pyproject.
- Commit messages: conventional (`feat(tools): …`, `test(eval): …`), each ending with the trailer `Co-Authored-By: Claude <noreply@anthropic.com>`.

## File Structure

```
src/edgar/
├── config.py                  # MODIFY: model ids, agent knobs, load_secrets_env()
├── pipeline.py                # MODIFY: rebuild_curated()
├── curate/mapping.py          # MODIFY: 15 fields, MR-0020..MR-0030
├── query/coverage.py          # MODIFY: hints for 5 new fields
├── quality/dictionary.py      # MODIFY: 5 new FIELD_DEFINITIONS
├── tools/
│   ├── schemas.py             # Pydantic DTOs shared by tools/agent/eval
│   ├── facts_tools.py         # get_facts, list_available_facts
│   ├── compute.py             # safe evaluator + derivation table
│   └── peers.py               # get_peer_set
├── narrative/
│   ├── store.py               # narrative_doc + span DDL, chunker, indexing, hybrid search
│   ├── fetch.py               # edgartools fetch (injectable fetcher)
│   └── embedder.py            # Embedder protocol, FakeEmbedder, SentenceTransformerEmbedder
├── memory/
│   ├── episodic.py            # session, session_conclusion, recall_conclusions
│   └── procedural.py          # prompt/rubric loader
├── agent/
│   ├── llm.py                 # LLMClient protocol, AnthropicLLM, FakeLLM
│   ├── agent_config.py        # versioned config loader (config/versions/*.yaml)
│   ├── ledger.py              # EvidenceLedger + compaction
│   ├── memo.py                # Memo/Claim models + markdown renderer
│   ├── guardrails.py          # deterministic reply-path checks + repair
│   ├── nodes.py               # graph node functions (plain, testable)
│   ├── graph.py               # LangGraph wiring
│   └── run.py                 # CLI entry point
├── ops/tracing.py             # Tracer protocol, Noop/Recording/Langfuse
└── eval/
    ├── schemas.py             # RawClaim, Verdict, EvalReport
    ├── decompose.py           # memo markdown -> atomic typed claims (judge model)
    ├── judges.py              # numeric/derived/attributed/inferential + temporal
    ├── metrics.py             # aggregate + render report
    ├── adversarial.py         # 30-question refusal-vs-fabrication runner
    └── calibration.py         # sampling + Cohen's kappa

prompts/                       # procedural memory (system + 11 section rubrics)
config/versions/v1.yaml        # versioned agent config (§9.2)
data/adversarial.yaml          # committed eval asset (NOT gitignored: lives at eval/assets/)
docker-compose.langfuse.yml    # self-hosted tracing backend
tests/test_<module>.py         # one file per new module (matches Stage 1 convention)
```

Existing interfaces you will consume (already built and tested — do not modify unless a task says so):

```python
# edgar.db
connect(path: Path | None = None) -> duckdb.DuckDBPyConnection
init_schema(con) -> None                      # creates raw_* tables

# edgar.query.asof
get_facts_asof(con, cik: int, fields: list[str], period_start: date,
               period_end: date, as_of: date) -> list[AsOfFact]
# AsOfFact: fact_id, cik, canonical_field, value, unit, period_type ("instant"|"duration"),
#           period_start (date|None), period_end, filed_date, accession, source_tag, mapping_rule_id

# edgar.query.coverage
coverage_map(con, cik: int, period_end: date, as_of: date) -> dict[str, FieldStatus]
# FieldStatus: AVAILABLE | NOT_DISCLOSED | NOT_YET_FILED | UNMAPPED | AMBIGUOUS

# edgar.curate.mapping
CANONICAL_FIELDS: tuple[str, ...]      # 10 now, 15 after Task 1
MappingRule(mapping_rule_id, source_tag, taxonomy, canonical_field,
            sign_convention, scale, method, confidence, priority, rationale)
seed_mapping_rules(con) -> int         # DELETEs method='deterministic' then re-inserts SEED_RULES
create_mapping_table(con), create_fact_table(con)  # (fact table via edgar.curate.facts)

# edgar.curate.facts
build_facts(con) -> int                # full projection, INSERT OR REPLACE, priority-arbitrated
FACT_DDL                               # fact table: 16 cols incl. fiscal_year, fiscal_period

# edgar.curate.company / universe
build_company_table(con) -> int        # company(cik,name,sic,sector,fiscal_year_end_month,...)
apply_eligibility(con) -> dict         # sets eligibility_status/exclusion_reason

# edgar.curate.periods
PeriodType.INSTANT == "instant"; PeriodType.DURATION == "duration"
parse_period(ddate: str, qtrs: str) -> Period(period_type, start: date|None, end: date)

# edgar.config
get_settings() -> Settings   # data_dir, duckdb_path, sec_user_agent, start_year, start_quarter
                             # lru_cached — tests construct Settings() directly or pass paths
```

A reusable test helper you will re-create in several test files (Stage 1 convention is module-level `_helpers`, no conftest — follow it):

```python
# Seeds a minimal curated store. Copy into each test file that needs it.
from datetime import date
from edgar.db import connect
from edgar.curate.facts import create_fact_table

def _db(tmp_path):
    con = connect(tmp_path / "t.duckdb")
    create_fact_table(con)
    return con

def _fact(con, *, fact_id, cik=1, field="revenue", value=100.0, unit="USD",
          ptype="duration", pstart=date(2023, 1, 1), pend=date(2023, 3, 31),
          filed=date(2023, 5, 1), accn="a-1", tag="Revenues", rule="MR-0003"):
    con.execute(
        "INSERT INTO fact VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [fact_id, cik, field, value, unit, ptype, pstart, pend,
         "2023", "Q1", filed, accn, tag, rule, 1.0, "2023q2"],
    )
```

---

### Task 1: Canonical schema expansion — 5 fields, 11 mapping rules

Spec §4.4 (rev 3) and task 2.0. Adds `inventory`, `accounts_receivable`, `accounts_payable`, `long_term_debt`, `cash_and_equivalents`. **Note the deviation from the spec's field name:** the spec says `total_debt`, but measured against the built database no single GAAP tag expresses total debt for more than ~9% of companies (`DebtLongtermAndShorttermCombinedAmount`: 76 of 11,119 ciks) — the concept is filed as components. The honest single-tag field is `long_term_debt` with tiered tags. This task also amends the spec so code and spec do not drift.

Tag coverage measured 2026-08-19 against the full 2019q1–2026q1 store (11,119 ciks; company-level rows only):

| Tag | ciks | Maps to | Priority | Note |
|---|---|---|---|---|
| `InventoryNet` | 4,123 | inventory | 1 | |
| `AccountsReceivableNetCurrent` | 5,476 | accounts_receivable | 1 | |
| `ReceivablesNetCurrent` | 842 | accounts_receivable | 2 | broader (incl. notes) |
| `AccountsPayableCurrent` | 6,116 | accounts_payable | 1 | |
| `AccountsPayableTradeCurrent` | 392 | accounts_payable | 2 | narrower (trade only) |
| `CashAndCashEquivalentsAtCarryingValue` | 9,117 | cash_and_equivalents | 1 | |
| `CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents` | 8,053 | cash_and_equivalents | 2 | overstates: includes restricted |
| `Cash` | 2,718 | cash_and_equivalents | 3 | understates: excludes equivalents |
| `LongTermDebt` | 1,031 | long_term_debt | 1 | total LTD incl. current maturities |
| `LongTermDebtAndCapitalLeaseObligations` | 744 | long_term_debt | 2 | broader: incl. finance leases |
| `LongTermDebtNoncurrent` | 3,097 | long_term_debt | 3 | understates: excludes current portion |

Deliberately NOT mapped: `AccountsPayableAndAccruedLiabilitiesCurrent` (2,302 ciks) — conflates payables with accrued liabilities and would corrupt days-payable arithmetic; leave it to surface as `UNMAPPED`.

**Files:**
- Modify: `src/edgar/curate/mapping.py` (CANONICAL_FIELDS + SEED_RULES)
- Modify: `src/edgar/query/coverage.py` (`_CONCEPT_HINTS` — `_looks_related` raises KeyError for any field without a hints entry)
- Modify: `src/edgar/quality/dictionary.py` (`FIELD_DEFINITIONS` — `generate_data_dictionary` raises KeyError for any field without a definition)
- Modify: `src/edgar/pipeline.py` (add `rebuild_curated`)
- Modify: `Makefile` (add `rebuild-curated`)
- Modify: `tests/test_mapping.py` (10→15 assertions), `tests/test_pipeline_rebuild.py` (new)
- Modify: `docs/superpowers/specs/2026-08-18-cited-diligence-agent-design.md` (§4.4 rows: `total_debt` → `long_term_debt`, with the measured rationale above)

**Interfaces:**
- Consumes: existing `MappingRule`, `_r()` helper, `seed_mapping_rules`, `build_facts`.
- Produces: `CANONICAL_FIELDS` with 15 entries in this exact order — the original 10 unchanged, then `"inventory", "accounts_receivable", "accounts_payable", "long_term_debt", "cash_and_equivalents"`. `rebuild_curated(con) -> dict` in `edgar.pipeline`. Later tasks (tools, memo rubrics) rely on these exact field names.

- [ ] **Step 1: Write the failing tests**

Update `tests/test_mapping.py`: change `test_exactly_ten_canonical_fields` to:

```python
def test_exactly_fifteen_canonical_fields():
    assert len(CANONICAL_FIELDS) == 15
    assert CANONICAL_FIELDS[:10] == (
        "revenue", "cost_of_revenue", "gross_profit", "operating_income",
        "net_income", "total_assets", "total_liabilities",
        "stockholders_equity", "operating_cash_flow", "capex",
    )
    assert CANONICAL_FIELDS[10:] == (
        "inventory", "accounts_receivable", "accounts_payable",
        "long_term_debt", "cash_and_equivalents",
    )

def test_new_field_priorities():
    assert rules_for_tag("InventoryNet")[0].canonical_field == "inventory"
    assert rules_for_tag("AccountsReceivableNetCurrent")[0].canonical_field == "accounts_receivable"
    assert rules_for_tag("AccountsPayableCurrent")[0].canonical_field == "accounts_payable"
    assert rules_for_tag("CashAndCashEquivalentsAtCarryingValue")[0].canonical_field == "cash_and_equivalents"
    ltd = sorted((r for r in SEED_RULES if r.canonical_field == "long_term_debt"),
                 key=lambda r: r.priority)
    assert [r.source_tag for r in ltd] == [
        "LongTermDebt", "LongTermDebtAndCapitalLeaseObligations",
        "LongTermDebtNoncurrent"]

def test_accrued_liabilities_combo_tag_is_not_mapped():
    assert rules_for_tag("AccountsPayableAndAccruedLiabilitiesCurrent") == []

def test_thirty_rules_total():
    assert len(SEED_RULES) == 30
```

Add `tests/test_pipeline_rebuild.py`:

```python
from datetime import date
from edgar.db import connect, init_schema
from edgar.pipeline import rebuild_curated

def _seed_raw(con):
    init_schema(con)
    con.execute("INSERT INTO raw_sub VALUES ('a-1','1','ACME','3571','1231',"
                "'10-Q','20230331','2023','Q1','20230501','0','1','1','2023q2')")
    # An instant fact under a NEW tag: proves rebuild picks up new rules.
    con.execute("INSERT INTO raw_num VALUES ('a-1','InventoryNet','us-gaap/2023',"
                "NULL,'20230331','0','USD','500',NULL,NULL,'2023q2')")

def test_rebuild_creates_facts_for_new_fields(tmp_path):
    con = connect(tmp_path / "t.duckdb")
    _seed_raw(con)
    report = rebuild_curated(con)
    row = con.execute("SELECT canonical_field, value, period_type, period_start "
                      "FROM fact").fetchone()
    assert row[0] == "inventory" and row[1] == 500.0
    assert row[2] == "instant" and row[3] is None
    assert report["facts"] >= 1 and "eligibility" in report

def test_rebuild_is_idempotent(tmp_path):
    con = connect(tmp_path / "t.duckdb")
    _seed_raw(con)
    rebuild_curated(con)
    n1 = con.execute("SELECT count(*) FROM fact").fetchone()[0]
    rebuild_curated(con)
    assert con.execute("SELECT count(*) FROM fact").fetchone()[0] == n1
```

- [ ] **Step 2: Run to verify failure**

Run: `venv/bin/pytest tests/test_mapping.py tests/test_pipeline_rebuild.py -v`
Expected: FAIL — `test_exactly_fifteen_canonical_fields` (len 10), `ImportError: cannot import name 'rebuild_curated'`.

- [ ] **Step 3: Implement**

In `src/edgar/curate/mapping.py`, extend the tuple and add rules MR-0020..MR-0030 (same `_r` helper; all instants, sign 1, scale 1.0):

```python
CANONICAL_FIELDS: tuple[str, ...] = (
    "revenue", "cost_of_revenue", "gross_profit", "operating_income",
    "net_income", "total_assets", "total_liabilities",
    "stockholders_equity", "operating_cash_flow", "capex",
    "inventory", "accounts_receivable", "accounts_payable",
    "long_term_debt", "cash_and_equivalents",
)
```

Append to `SEED_RULES` (inside the existing tuple, after rule 19):

```python
    _r(20, "InventoryNet", "inventory", 1,
       "Net inventory at the balance sheet date; the dominant aggregate tag "
       "(4,123 of 11,119 ciks, 2019-2026 store)."),
    _r(21, "AccountsReceivableNetCurrent", "accounts_receivable", 1,
       "Net current trade receivables; dominant tag (5,476 ciks)."),
    _r(22, "ReceivablesNetCurrent", "accounts_receivable", 2,
       "Broader current receivables aggregate (incl. notes); used when the "
       "trade-specific tag is absent (842 ciks)."),
    _r(23, "AccountsPayableCurrent", "accounts_payable", 1,
       "Current accounts payable; dominant tag (6,116 ciks). The combined "
       "AccountsPayableAndAccruedLiabilitiesCurrent tag is deliberately NOT "
       "mapped: it conflates payables with accrued liabilities and would "
       "corrupt days-payable arithmetic."),
    _r(24, "AccountsPayableTradeCurrent", "accounts_payable", 2,
       "Trade-only payables; narrower, used when the aggregate is absent."),
    _r(25, "LongTermDebt", "long_term_debt", 1,
       "Total long-term debt including current maturities. Field is named "
       "long_term_debt, not total_debt: no single GAAP tag expresses "
       "ST+LT total debt for more than a few hundred filers; short-term "
       "borrowings are excluded by construction."),
    _r(26, "LongTermDebtAndCapitalLeaseObligations", "long_term_debt", 2,
       "Broader: includes finance-lease obligations; used when the pure "
       "debt total is absent."),
    _r(27, "LongTermDebtNoncurrent", "long_term_debt", 3,
       "Noncurrent portion only (3,097 ciks — the most common form). "
       "Understates by current maturities when the total tags are absent."),
    _r(28, "CashAndCashEquivalentsAtCarryingValue", "cash_and_equivalents", 1,
       "Unrestricted cash and equivalents; dominant tag (9,117 ciks)."),
    _r(29, "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
        "cash_and_equivalents", 2,
        "Cash-flow-statement reconciliation total; overstates by restricted "
        "cash when the unrestricted tag is absent."),
    _r(30, "Cash", "cash_and_equivalents", 3,
       "Bare cash; understates by excluding equivalents. Legacy filers."),
```

In `src/edgar/query/coverage.py`, extend `_CONCEPT_HINTS` (keys must cover every canonical field or `_looks_related` raises):

```python
    "inventory": ("inventory",),
    "accounts_receivable": ("accountsreceivable", "receivablesnet"),
    "accounts_payable": ("accountspayable",),
    "long_term_debt": ("longtermdebt", "notespayable", "borrowings",
                       "seniornotes"),
    "cash_and_equivalents": ("cashandcashequivalents", "cashcashequivalents"),
```

In `src/edgar/quality/dictionary.py`, extend `FIELD_DEFINITIONS`:

```python
    "inventory": "Net inventory held at the balance sheet date. Instant fact.",
    "accounts_receivable": "Amounts owed by customers, net, current. "
                           "Instant fact. Broader ReceivablesNetCurrent is "
                           "used when the trade-specific tag is absent.",
    "accounts_payable": "Amounts owed to suppliers, current. Instant fact. "
                        "The combined payables+accrued-liabilities tag is "
                        "deliberately not mapped (would overstate).",
    "long_term_debt": "Long-term borrowings. Instant fact. Named "
                      "long_term_debt, not total_debt: short-term borrowings "
                      "are excluded by construction, and when only "
                      "LongTermDebtNoncurrent is filed the current portion "
                      "is excluded too. See mapping rationale MR-0025.",
    "cash_and_equivalents": "Unrestricted cash and cash equivalents. Instant "
                            "fact. Fallback tags may include restricted cash "
                            "(overstates) or exclude equivalents "
                            "(understates); see MR-0029/MR-0030.",
```

In `src/edgar/pipeline.py`, add after `build_all`:

```python
def rebuild_curated(con) -> dict:
    """Rebuild the curated zone from an already-loaded raw zone.

    For mapping-rule changes: full fact rebuild without re-downloading or
    re-loading 29 quarters of archives. Deletes fact rows first so facts
    from removed rules cannot linger (build_facts alone only ever adds).
    """
    create_mapping_table(con)
    seed_mapping_rules(con)
    create_fact_table(con)
    con.execute("DELETE FROM fact")
    n_facts = build_facts(con)
    n_companies = build_company_table(con)
    return {
        "companies": n_companies,
        "facts": n_facts,
        "coverage": mapping_coverage(con),
        "eligibility": apply_eligibility(con),
        "quality": [r.__dict__ for r in run_quality_checks(con)],
    }
```

Makefile target (tab-indented recipe):

```make
rebuild-curated:
	venv/bin/python -c "from edgar.db import connect; from edgar.config import get_settings; from edgar.pipeline import rebuild_curated; import json; print(json.dumps(rebuild_curated(connect(get_settings().duckdb_path)), indent=2, default=str))"
```

Amend spec §4.4: replace the `total_debt` row with `long_term_debt` and add one sentence citing the 76-cik measurement; update the §16 rev-3 field list to match.

- [ ] **Step 4: Run the full suite**

Run: `venv/bin/pytest -q`
Expected: all pass (the 137 existing + new). If `test_dictionary` or `test_coverage` fail on the new fields, the hints/definitions edits above are incomplete — fix there, not in the tests.

- [ ] **Step 5: Rebuild the real store and eyeball coverage**

Run: `make rebuild-curated` (several minutes; scans 95M raw rows).
Then verify the new fields materialized:

```bash
venv/bin/python -c "
import duckdb; from edgar.config import get_settings
con = duckdb.connect(str(get_settings().duckdb_path), read_only=True)
con.sql(\"select canonical_field, count(*) n, count(distinct cik) ciks from fact where canonical_field in ('inventory','accounts_receivable','accounts_payable','long_term_debt','cash_and_equivalents') group by 1 order by 1\").show()"
```

Expected: ciks per field in the same order of magnitude as the tag-coverage table above (inventory ≈4.1k, receivables ≈5.5k+, payables ≈6.1k+, long_term_debt ≈3.5k, cash ≈9k+). Also regenerate the dictionary: `venv/bin/python -m edgar.quality.dictionary`.

- [ ] **Step 6: Commit**

```bash
git add src/edgar/curate/mapping.py src/edgar/query/coverage.py \
  src/edgar/quality/dictionary.py src/edgar/pipeline.py Makefile \
  tests/test_mapping.py tests/test_pipeline_rebuild.py docs/data-dictionary.md \
  docs/superpowers/specs/2026-08-18-cited-diligence-agent-design.md
git commit -m "feat(mapping): expand canonical schema to 15 fields

total_debt renamed long_term_debt: measured against the full store, no
single GAAP tag expresses ST+LT total debt for >9% of filers.

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 2: Settings, secrets loader, dependency pins

**Files:**
- Modify: `src/edgar/config.py`
- Modify: `pyproject.toml`
- Test: `tests/test_config.py` (new)

**Interfaces:**
- Consumes: existing `Settings` / `get_settings()`.
- Produces (exact names later tasks import):
  - `Settings.generation_model: str = "claude-opus-5"`
  - `Settings.judge_model: str = "claude-sonnet-5"`
  - `Settings.narrative_ciks: tuple[int, ...]` — default `(320193, 789019, 1045810, 1318605, 77476, 200406, 354950, 909832, 1018724, 1652044)` (AAPL, MSFT, NVDA, TSLA, PEP, JNJ, HD, COST, AMZN, GOOGL — all eligible, sector- and fiscal-calendar-diverse, verified against the store 2026-08-19)
  - `load_secrets_env(path: Path | None = None) -> list[str]` — parses `.env` for `ANTHROPIC_API_KEY`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST` and `os.environ.setdefault`s each; returns names it set. (pydantic-settings only binds `EDGAR_`-prefixed keys; the anthropic and langfuse SDKs read their own env vars directly.)

- [ ] **Step 1: Failing tests** — `tests/test_config.py`:

```python
import os
from edgar.config import Settings, load_secrets_env

def test_model_defaults():
    s = Settings(_env_file=None)
    assert s.generation_model == "claude-opus-5"
    assert s.judge_model == "claude-sonnet-5"
    assert len(s.narrative_ciks) == 10 and 320193 in s.narrative_ciks

def test_load_secrets_env_sets_and_reports(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    env = tmp_path / ".env"
    env.write_text("ANTHROPIC_API_KEY=sk-test\n# comment\nEDGAR_DATA_DIR=x\n")
    set_names = load_secrets_env(env)
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-test"
    assert set_names == ["ANTHROPIC_API_KEY"]

def test_load_secrets_env_never_overrides(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-shell")
    env = tmp_path / ".env"
    env.write_text("ANTHROPIC_API_KEY=sk-file\n")
    assert load_secrets_env(env) == []
    assert os.environ["ANTHROPIC_API_KEY"] == "from-shell"

def test_load_secrets_env_missing_file_is_noop(tmp_path):
    assert load_secrets_env(tmp_path / "absent.env") == []
```

- [ ] **Step 2: Run to verify failure**

Run: `venv/bin/pytest tests/test_config.py -v` — Expected: FAIL (`AttributeError` / `ImportError`).

- [ ] **Step 3: Implement** — in `src/edgar/config.py` add fields and function:

```python
_SECRET_KEYS = ("ANTHROPIC_API_KEY", "LANGFUSE_PUBLIC_KEY",
                "LANGFUSE_SECRET_KEY", "LANGFUSE_HOST")


class Settings(BaseSettings):
    ...  # existing fields unchanged
    generation_model: str = "claude-opus-5"
    judge_model: str = "claude-sonnet-5"
    # Fixed Stage 2 narrative/eval set (spec §4.9): eligible, sector- and
    # fiscal-calendar-diverse, all 10 v1 fields present. Verified 2026-08-19.
    narrative_ciks: tuple[int, ...] = (
        320193, 789019, 1045810, 1318605, 77476,
        200406, 354950, 909832, 1018724, 1652044,
    )


def load_secrets_env(path: Path | None = None) -> list[str]:
    """os.environ.setdefault unprefixed secrets from .env.

    pydantic-settings binds only EDGAR_-prefixed keys; the anthropic and
    langfuse SDKs read their own env vars. Shell env always wins.
    """
    import os
    target = path if path is not None else Path(".env")
    if not target.exists():
        return []
    loaded: list[str] = []
    for line in target.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key in _SECRET_KEYS and key not in os.environ:
            os.environ[key] = value.strip()
            loaded.append(key)
    return loaded
```

In `pyproject.toml`: remove the unused `pandas>=2.2` from `dependencies` (declared but imported nowhere — verified by grep); add `"langgraph>=1.2,<2"`, `"anthropic>=0.125"`, `"pyyaml>=6"`, `"langfuse>=4,<5"`; add optional group:

```toml
[project.optional-dependencies]
narrative = ["edgartools>=5.51", "sentence-transformers>=6"]
```

(keep the existing `dev` extra as is). Then `venv/bin/pip install -e ".[dev]"`.

- [ ] **Step 4: Run** — `venv/bin/pytest tests/test_config.py -q` then full `venv/bin/pytest -q`. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/edgar/config.py pyproject.toml tests/test_config.py
git commit -m "feat(config): model ids, narrative set, secrets loader; drop unused pandas

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 3: Tool DTOs + `get_facts` + `list_available_facts`

Spec §6. Pydantic schemas are the tool contract for the agent, the guardrails, and the eval — get them right here and every later task consumes them unchanged.

**Files:**
- Create: `src/edgar/tools/__init__.py` (empty), `src/edgar/tools/schemas.py`, `src/edgar/tools/facts_tools.py`
- Test: `tests/test_facts_tools.py`

**Interfaces:**
- Consumes: `get_facts_asof`, `coverage_map`, `CANONICAL_FIELDS`.
- Produces (all pydantic `BaseModel`, `model_config = ConfigDict(frozen=True)`):

```python
# edgar.tools.schemas
class FactDTO(BaseModel):
    fact_id: str; cik: int; canonical_field: str; value: float; unit: str
    period_type: str; period_start: date | None; period_end: date
    filed_date: date; accession: str; source_tag: str

class MissingField(BaseModel):
    canonical_field: str; status: str          # FieldStatus value

class GetFactsResult(BaseModel):
    facts: list[FactDTO]; missing: list[MissingField]

class CoverageEntry(BaseModel):
    period_end: date; statuses: dict[str, str]  # field -> FieldStatus value

class CoverageReport(BaseModel):
    cik: int; as_of: date; entries: list[CoverageEntry]  # newest period first

class SpanDTO(BaseModel):
    span_id: str; cik: int; accession: str; form: str; item: str
    filed_date: date; char_start: int; char_end: int; text: str

class Computation(BaseModel):
    derivation_id: str; expression: str
    inputs: dict[str, str]                      # var name -> fact_id
    values: dict[str, float]                    # var name -> substituted value
    value: float; as_of: date

class Peer(BaseModel):
    cik: int; name: str; sic: str; fiscal_year_end_month: int | None

class PeerSet(BaseModel):
    cik: int; as_of: date; peers: list[Peer]; selection_rule: str

# edgar.tools.facts_tools
get_facts(con, cik: int, fields: list[str], period_start: date,
          period_end: date, as_of: date) -> GetFactsResult
list_available_facts(con, cik: int, as_of: date,
                     max_periods: int = 24) -> CoverageReport
```

- [ ] **Step 1: Failing tests** — `tests/test_facts_tools.py` (use the `_db`/`_fact` helper from the File Structure section):

```python
from datetime import date
from edgar.tools.facts_tools import get_facts, list_available_facts
# ... paste _db/_fact helper here ...

def test_get_facts_returns_visible_fact_and_flags_missing(tmp_path):
    con = _db(tmp_path)
    _fact(con, fact_id="f1", field="revenue", filed=date(2023, 5, 1))
    r = get_facts(con, 1, ["revenue", "inventory"],
                  date(2023, 1, 1), date(2023, 3, 31), as_of=date(2023, 6, 1))
    assert [f.fact_id for f in r.facts] == ["f1"]
    missing = {m.canonical_field: m.status for m in r.missing}
    assert missing == {"inventory": "NOT_DISCLOSED"}

def test_get_facts_hides_later_filings_and_reports_not_yet_filed(tmp_path):
    con = _db(tmp_path)
    _fact(con, fact_id="f1", field="revenue", filed=date(2023, 5, 1))
    r = get_facts(con, 1, ["revenue"], date(2023, 1, 1), date(2023, 3, 31),
                  as_of=date(2023, 4, 1))   # before the filing
    assert r.facts == []
    assert r.missing[0].status == "NOT_YET_FILED"

def test_list_available_facts_orders_new_to_old_and_respects_as_of(tmp_path):
    con = _db(tmp_path)
    _fact(con, fact_id="q1", pend=date(2023, 3, 31), filed=date(2023, 5, 1))
    _fact(con, fact_id="q2", pend=date(2023, 6, 30), filed=date(2023, 8, 1))
    rep = list_available_facts(con, 1, as_of=date(2023, 6, 1))
    assert [e.period_end for e in rep.entries] == [date(2023, 3, 31)]
    assert rep.entries[0].statuses["revenue"] == "AVAILABLE"
    assert rep.entries[0].statuses["inventory"] == "NOT_DISCLOSED"

def test_get_facts_rejects_unknown_field(tmp_path):
    con = _db(tmp_path)
    import pytest
    with pytest.raises(ValueError, match="unknown canonical field"):
        get_facts(con, 1, ["ebitda"], date(2023, 1, 1), date(2023, 3, 31),
                  as_of=date(2023, 6, 1))
```

- [ ] **Step 2: Run to verify failure** — `venv/bin/pytest tests/test_facts_tools.py -v` → ImportError.

- [ ] **Step 3: Implement**

`src/edgar/tools/schemas.py`: exactly the models in the Interfaces block (import `date` from `datetime`, `BaseModel, ConfigDict` from pydantic; every model `model_config = ConfigDict(frozen=True)`).

`src/edgar/tools/facts_tools.py`:

```python
from datetime import date
import duckdb
from edgar.curate.mapping import CANONICAL_FIELDS
from edgar.query.asof import get_facts_asof
from edgar.query.coverage import coverage_map
from edgar.tools.schemas import (
    FactDTO, MissingField, GetFactsResult, CoverageEntry, CoverageReport)


def _validate_fields(fields: list[str]) -> None:
    unknown = [f for f in fields if f not in CANONICAL_FIELDS]
    if unknown:
        raise ValueError(f"unknown canonical field(s): {unknown}; "
                         f"valid: {list(CANONICAL_FIELDS)}")


def get_facts(con: duckdb.DuckDBPyConnection, cik: int, fields: list[str],
              period_start: date, period_end: date, as_of: date) -> GetFactsResult:
    """Spec §6: missing values return a §4.6 status, never silence.

    The status for a missing field is classified at the latest period_end
    the company has EVER filed inside the window (visible or not — a
    later-filed row is exactly what NOT_YET_FILED must detect). With no
    period at all in the window, the field is NOT_DISCLOSED.
    """
    _validate_fields(fields)
    facts = [FactDTO(fact_id=a.fact_id, cik=a.cik, canonical_field=a.canonical_field,
                     value=a.value, unit=a.unit, period_type=a.period_type,
                     period_start=a.period_start, period_end=a.period_end,
                     filed_date=a.filed_date, accession=a.accession,
                     source_tag=a.source_tag)
             for a in get_facts_asof(con, cik, fields, period_start, period_end, as_of)]
    present = {f.canonical_field for f in facts}
    missing_fields = [f for f in fields if f not in present]
    missing: list[MissingField] = []
    if missing_fields:
        ref = con.execute(
            "SELECT max(period_end) FROM fact WHERE cik = ? "
            "AND period_end BETWEEN ? AND ?",
            [cik, period_start, period_end]).fetchone()[0]
        if ref is None:
            missing = [MissingField(canonical_field=f, status="NOT_DISCLOSED")
                       for f in missing_fields]
        else:
            statuses = coverage_map(con, cik, ref, as_of)
            missing = [MissingField(canonical_field=f, status=str(statuses[f]))
                       for f in missing_fields]
    return GetFactsResult(facts=facts, missing=missing)


def list_available_facts(con: duckdb.DuckDBPyConnection, cik: int, as_of: date,
                         max_periods: int = 24) -> CoverageReport:
    """Spec §6: the coverage map that makes refusal reachable."""
    period_ends = [r[0] for r in con.execute(
        "SELECT DISTINCT period_end FROM fact WHERE cik = ? "
        "AND filed_date <= ? ORDER BY period_end DESC LIMIT ?",
        [cik, as_of, max_periods]).fetchall()]
    entries = [CoverageEntry(
                   period_end=pe,
                   statuses={k: str(v) for k, v in
                             coverage_map(con, cik, pe, as_of).items()})
               for pe in period_ends]
    return CoverageReport(cik=cik, as_of=as_of, entries=entries)
```

Note: `coverage_map` needs the raw tables for its UNMAPPED heuristic; on a fact-only test DB the `raw_num` query would fail — it does not, because `connect()`+`create_fact_table` leaves `raw_num` absent. **Therefore `_db` in this test file must also call `init_schema(con)`** so `raw_sub`/`raw_num` exist (empty). Add `from edgar.db import init_schema` and call it inside `_db`.

- [ ] **Step 4: Run** — `venv/bin/pytest tests/test_facts_tools.py -q` → PASS; then full suite.

- [ ] **Step 5: Commit**

```bash
git add src/edgar/tools tests/test_facts_tools.py
git commit -m "feat(tools): DTO schemas, get_facts, list_available_facts

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 4: `compute` — deterministic arithmetic over fact IDs

Spec §6: "The model does no arithmetic." Every derived number gets a persisted derivation record (§5 lineage) that guardrails and the eval recompute independently.

**Type rule (refines spec §4.4/§6, and record this in the spec):** the blanket "duration and instant never mix" would make the spec's own memo sections 5–6 uncomputable — asset turnover is revenue (duration) ÷ assets (instant); days-inventory is inventory (instant) ÷ COGS (duration) × days. The enforceable invariant is: **`+` and `-` require identical `period_type` among their operands; `*` and `/` are unrestricted.** Additive mixing is the modeling bug; ratios across types are the standard metrics.

**Cross-company guard (spec §4.8):** if inputs span more than one cik, every pair of inputs must end in the same calendar quarter, and duration inputs must have equal length; otherwise reject.

**Files:**
- Create: `src/edgar/tools/compute.py`
- Test: `tests/test_compute.py`
- Modify: `docs/superpowers/specs/2026-08-18-cited-diligence-agent-design.md` — §6 `compute` bullet: replace "Rejects duration/instant mixing" with "Rejects additive (+/−) mixing of duration and instant operands; ratios across types are permitted (asset turnover, days metrics require them)". One-line addition to §16 rev log.

**Interfaces:**
- Consumes: `fact` table, `Computation` DTO (Task 3).
- Produces:

```python
DERIVATION_DDL: str          # table derivation(derivation_id PK, expression,
                             #   inputs_json, value DOUBLE, as_of DATE)
create_derivation_table(con) -> None
class ComputeError(ValueError): ...
compute(con, expression: str, inputs: dict[str, str], as_of: date) -> Computation
recompute(con, derivation_id: str) -> Computation      # from the stored record
```

`derivation_id` = `"D-" + sha256(expression|sorted(inputs)|as_of)[:16]` — content-addressed, so identical computations collapse to one row (`INSERT OR REPLACE`).

**Expression grammar:** Python `ast` parsed with `mode="eval"`; allowed nodes: `Expression, BinOp, UnaryOp, Add, Sub, Mult, Div, USub, Name, Constant(int|float), ParenthesizedExpr-implicit`. Anything else (calls, attributes, comparisons, strings) → `ComputeError`. Names must all appear in `inputs`; unused inputs are an error too (a cited input that did not participate is a lie in the lineage).

- [ ] **Step 1: Failing tests** — `tests/test_compute.py` (reuse `_db`/`_fact` helper; `_db` needs only `create_fact_table`):

```python
import pytest
from datetime import date
from edgar.tools.compute import (
    compute, recompute, create_derivation_table, ComputeError)
# ... _db/_fact helper ...

def _setup(tmp_path):
    con = _db(tmp_path)
    create_derivation_table(con)
    _fact(con, fact_id="gp", field="gross_profit", value=40.0)
    _fact(con, fact_id="rev", field="revenue", value=100.0)
    _fact(con, fact_id="inv", field="inventory", value=30.0,
          ptype="instant", pstart=None)
    return con

def test_margin_with_derivation_record(tmp_path):
    con = _setup(tmp_path)
    c = compute(con, "gp / rev", {"gp": "gp", "rev": "rev"}, date(2023, 6, 1))
    assert c.value == pytest.approx(0.4)
    assert c.values == {"gp": 40.0, "rev": 100.0}
    assert c.derivation_id.startswith("D-")
    again = recompute(con, c.derivation_id)
    assert again.value == pytest.approx(0.4)

def test_rejects_additive_type_mixing_but_allows_ratio(tmp_path):
    con = _setup(tmp_path)
    with pytest.raises(ComputeError, match="period_type"):
        compute(con, "rev + inv", {"rev": "rev", "inv": "inv"}, date(2023, 6, 1))
    days = compute(con, "inv / rev * 91", {"inv": "inv", "rev": "rev"},
                   date(2023, 6, 1))
    assert days.value == pytest.approx(27.3)

def test_rejects_input_filed_after_as_of(tmp_path):
    con = _setup(tmp_path)
    with pytest.raises(ComputeError, match="filed after as_of"):
        compute(con, "rev * 1", {"rev": "rev"}, date(2023, 4, 1))

def test_rejects_unsafe_and_unknown(tmp_path):
    con = _setup(tmp_path)
    for expr, inputs in [
        ("__import__('os')", {}),
        ("rev.value", {"rev": "rev"}),
        ("rev + missing", {"rev": "rev"}),          # name not in inputs
        ("rev", {"rev": "rev", "gp": "gp"}),        # unused input
    ]:
        with pytest.raises(ComputeError):
            compute(con, expr, inputs, date(2023, 6, 1))
    with pytest.raises(ComputeError, match="no such fact"):
        compute(con, "x * 1", {"x": "nope"}, date(2023, 6, 1))

def test_cross_company_calendar_guard(tmp_path):
    con = _setup(tmp_path)
    _fact(con, fact_id="peer_rev", cik=2, field="revenue", value=50.0,
          pstart=date(2023, 4, 1), pend=date(2023, 6, 30))  # different quarter
    with pytest.raises(ComputeError, match="calendar"):
        compute(con, "rev / peer", {"rev": "rev", "peer": "peer_rev"},
                date(2023, 9, 1))

def test_division_by_zero_is_compute_error(tmp_path):
    con = _setup(tmp_path)
    _fact(con, fact_id="z", field="capex", value=0.0)
    with pytest.raises(ComputeError, match="division by zero"):
        compute(con, "rev / z", {"rev": "rev", "z": "z"}, date(2023, 6, 1))
```

- [ ] **Step 2: Run to verify failure** — ImportError expected.

- [ ] **Step 3: Implement** — `src/edgar/tools/compute.py`:

```python
import ast
import hashlib
import json
from datetime import date
import duckdb
from edgar.tools.schemas import Computation

DERIVATION_DDL = """
CREATE TABLE IF NOT EXISTS derivation (
    derivation_id VARCHAR PRIMARY KEY,
    expression VARCHAR,
    inputs_json VARCHAR,
    value DOUBLE,
    as_of DATE
);
"""


class ComputeError(ValueError):
    """Raised for any rejected computation. Message is agent-facing."""


def create_derivation_table(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(DERIVATION_DDL)


_ALLOWED_BINOPS = (ast.Add, ast.Sub, ast.Mult, ast.Div)


def _names(node: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def _eval(node, values, types):
    """Evaluate, returning (value, period_type|None). Constants carry None;
    +/- demand one period_type among non-constant operands."""
    if isinstance(node, ast.Expression):
        return _eval(node.body, values, types)
    if isinstance(node, ast.Constant):
        if not isinstance(node.value, (int, float)) or isinstance(node.value, bool):
            raise ComputeError(f"constant {node.value!r} not allowed")
        return float(node.value), None
    if isinstance(node, ast.Name):
        return values[node.id], types[node.id]
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        v, t = _eval(node.operand, values, types)
        return -v, t
    if isinstance(node, ast.BinOp) and isinstance(node.op, _ALLOWED_BINOPS):
        lv, lt = _eval(node.left, values, types)
        rv, rt = _eval(node.right, values, types)
        if isinstance(node.op, (ast.Add, ast.Sub)):
            if lt is not None and rt is not None and lt != rt:
                raise ComputeError(
                    f"additive mixing of period_type {lt!r} and {rt!r}; "
                    "add/subtract requires like types (ratios are allowed)")
            t = lt or rt
        else:
            t = None  # a ratio/product is dimensionally its own thing
        if isinstance(node.op, ast.Add):
            return lv + rv, t
        if isinstance(node.op, ast.Sub):
            return lv - rv, t
        if isinstance(node.op, ast.Mult):
            return lv * rv, t
        if rv == 0:
            raise ComputeError("division by zero")
        return lv / rv, t
    raise ComputeError(f"disallowed syntax: {ast.dump(node)[:80]}")


def _quarter(d: date) -> tuple[int, int]:
    return d.year, (d.month - 1) // 3


def compute(con: duckdb.DuckDBPyConnection, expression: str,
            inputs: dict[str, str], as_of: date) -> Computation:
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ComputeError(f"unparseable expression: {exc}") from exc
    used = _names(tree)
    if used != set(inputs):
        raise ComputeError(
            f"expression names {sorted(used)} must exactly match "
            f"inputs {sorted(inputs)}")

    rows: dict[str, tuple] = {}
    for var, fact_id in inputs.items():
        row = con.execute(
            "SELECT value, period_type, period_end, filed_date, cik, "
            "period_start FROM fact WHERE fact_id = ?", [fact_id]).fetchone()
        if row is None:
            raise ComputeError(f"no such fact: {fact_id} (input {var!r})")
        if row[3] > as_of:
            raise ComputeError(
                f"input {var!r} ({fact_id}) filed after as_of "
                f"({row[3]} > {as_of})")
        rows[var] = row

    ciks = {r[4] for r in rows.values()}
    if len(ciks) > 1:
        quarters = {_quarter(r[2]) for r in rows.values()}
        if len(quarters) > 1:
            raise ComputeError(
                "cross-company inputs must share a calendar quarter "
                f"(spec §4.8); got period_ends in quarters {sorted(quarters)}")
        lengths = {(r[2] - r[5]).days // 30 for r in rows.values()
                   if r[1] == "duration" and r[5] is not None}
        if len(lengths) > 1:
            raise ComputeError(
                "cross-company duration inputs must have equal length; "
                f"got ~{sorted(lengths)} months")

    values = {v: float(r[0]) for v, r in rows.items()}
    types = {v: r[1] for v, r in rows.items()}
    result, _ = _eval(tree, values, types)

    key = expression + "|" + json.dumps(dict(sorted(inputs.items()))) + f"|{as_of}"
    derivation_id = "D-" + hashlib.sha256(key.encode()).hexdigest()[:16]
    con.execute("INSERT OR REPLACE INTO derivation VALUES (?,?,?,?,?)",
                [derivation_id, expression,
                 json.dumps(dict(sorted(inputs.items()))), result, as_of])
    return Computation(derivation_id=derivation_id, expression=expression,
                       inputs=inputs, values=values, value=result, as_of=as_of)


def recompute(con: duckdb.DuckDBPyConnection, derivation_id: str) -> Computation:
    row = con.execute(
        "SELECT expression, inputs_json, as_of FROM derivation "
        "WHERE derivation_id = ?", [derivation_id]).fetchone()
    if row is None:
        raise ComputeError(f"no such derivation: {derivation_id}")
    return compute(con, row[0], json.loads(row[1]), row[2])
```

- [ ] **Step 4: Run** — `venv/bin/pytest tests/test_compute.py -q` → PASS (note `27.3 = 30/100*91`); then full suite.

- [ ] **Step 5: Commit**

```bash
git add src/edgar/tools/compute.py tests/test_compute.py \
  docs/superpowers/specs/2026-08-18-cited-diligence-agent-design.md
git commit -m "feat(tools): compute with derivation records; additive type-mixing rule

Spec §6 refined: +/- require like period_type, ratios cross types
(asset turnover and days metrics are duration/instant by definition).

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 5: `get_peer_set`

Spec §6: SIC-based, selection rule recorded. Calendar comparability is enforced downstream by `compute` (Task 4); this tool selects and documents.

**Files:**
- Create: `src/edgar/tools/peers.py`
- Test: `tests/test_peers.py`

**Interfaces:**
- Consumes: `company` table (cik, name, sic, sector, fiscal_year_end_month, eligibility_status), `fact` table, `Peer`/`PeerSet` DTOs.
- Produces: `get_peer_set(con, cik: int, as_of: date, min_peers: int = 10) -> PeerSet`. Selection: eligible companies sharing the subject's 2-digit SIC prefix, having ≥1 fact with `filed_date <= as_of`, excluding the subject, ordered by fact count desc, capped at `max(min_peers, 15)`. If fewer than `min_peers` match, widen to the subject's `sector` bucket and record that in `selection_rule`. Known limitation (documented in the docstring, spec §4.7): `company` is a latest-snapshot dimension, so sector/SIC are as of today, not as of `as_of`.

- [ ] **Step 1: Failing tests** — `tests/test_peers.py`:

```python
from datetime import date
from edgar.db import connect
from edgar.curate.facts import create_fact_table
from edgar.tools.peers import get_peer_set
# ... _fact helper from the shared block ...

def _company(con, cik, sic, sector="manufacturing", status="eligible"):
    con.execute(
        "INSERT INTO company VALUES (?,?,?,?,?,?,?,?)",
        [cik, f"CO{cik}", sic, sector, 12, date(2019, 1, 1), status, None])

def _db(tmp_path):
    con = connect(tmp_path / "t.duckdb")
    create_fact_table(con)
    con.execute("""CREATE TABLE company (cik BIGINT, name VARCHAR, sic VARCHAR,
        sector VARCHAR, fiscal_year_end_month INTEGER, first_filing_date DATE,
        eligibility_status VARCHAR, exclusion_reason VARCHAR)""")
    return con

def test_same_two_digit_sic_and_visibility(tmp_path):
    con = _db(tmp_path)
    _company(con, 1, "3571"); _company(con, 2, "3572"); _company(con, 3, "2911")
    _fact(con, fact_id="a", cik=2, filed=date(2023, 5, 1))
    _fact(con, fact_id="b", cik=3, filed=date(2023, 5, 1))
    ps = get_peer_set(con, 1, as_of=date(2023, 6, 1), min_peers=1)
    assert [p.cik for p in ps.peers] == [2]
    assert "35" in ps.selection_rule

def test_ineligible_and_unfiled_peers_excluded(tmp_path):
    con = _db(tmp_path)
    _company(con, 1, "3571")
    _company(con, 2, "3572", status="excluded")     # ineligible
    _company(con, 4, "3579")                         # eligible, no facts by as_of
    _fact(con, fact_id="a", cik=2, filed=date(2023, 5, 1))
    _fact(con, fact_id="c", cik=4, filed=date(2024, 5, 1))
    ps = get_peer_set(con, 1, as_of=date(2023, 6, 1), min_peers=1)
    assert ps.peers == []

def test_widens_to_sector_when_sic_too_thin(tmp_path):
    con = _db(tmp_path)
    _company(con, 1, "3571"); _company(con, 5, "2911")   # different SIC2, same sector
    _fact(con, fact_id="e", cik=5, filed=date(2023, 5, 1))
    ps = get_peer_set(con, 1, as_of=date(2023, 6, 1), min_peers=1)
    assert [p.cik for p in ps.peers] == [5]
    assert "sector" in ps.selection_rule
```

- [ ] **Step 2: Run to verify failure** — ImportError expected.

- [ ] **Step 3: Implement** — `src/edgar/tools/peers.py`:

```python
from datetime import date
import duckdb
from edgar.tools.schemas import Peer, PeerSet

_PEER_SQL = """
SELECT c.cik, c.name, c.sic, c.fiscal_year_end_month
FROM company c
JOIN (SELECT cik, count(*) AS n FROM fact
      WHERE filed_date <= ? GROUP BY cik) f ON f.cik = c.cik
WHERE c.eligibility_status = 'eligible' AND c.cik <> ? AND {predicate}
ORDER BY f.n DESC, c.cik
LIMIT ?
"""


def get_peer_set(con: duckdb.DuckDBPyConnection, cik: int, as_of: date,
                 min_peers: int = 10) -> PeerSet:
    """SIC-prefix peers, widening to sector when thin (spec §6).

    Known limitation: `company` is a latest-snapshot dimension (spec §4.7);
    sic/sector reflect today, not as_of. Facts visibility IS as-of-enforced.
    """
    row = con.execute(
        "SELECT sic, sector FROM company WHERE cik = ?", [cik]).fetchone()
    if row is None:
        raise ValueError(f"unknown cik {cik}")
    sic, sector = row
    cap = max(min_peers, 15)
    prefix = (str(sic).strip() or "??")[:2]
    rows = con.execute(_PEER_SQL.format(predicate="substr(c.sic, 1, 2) = ?"),
                       [as_of, cik, prefix, cap]).fetchall()
    rule = (f"eligible companies with 2-digit SIC prefix {prefix!r}, "
            f"≥1 fact filed on or before {as_of}, top {cap} by fact count")
    if len(rows) < min_peers:
        rows = con.execute(_PEER_SQL.format(predicate="c.sector = ?"),
                           [as_of, cik, sector, cap]).fetchall()
        rule = (f"SIC prefix {prefix!r} yielded <{min_peers}; widened to "
                f"sector {sector!r}, ≥1 fact filed on or before {as_of}, "
                f"top {cap} by fact count")
    peers = [Peer(cik=r[0], name=r[1], sic=r[2], fiscal_year_end_month=r[3])
             for r in rows]
    return PeerSet(cik=cik, as_of=as_of, peers=peers, selection_rule=rule)
```

- [ ] **Step 4: Run** — `venv/bin/pytest tests/test_peers.py -q` → PASS; full suite.

- [ ] **Step 5: Commit**

```bash
git add src/edgar/tools/peers.py tests/test_peers.py
git commit -m "feat(tools): get_peer_set with recorded selection rule

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 6: Narrative fetch — 10-K Items 1 / 1A / 7 via edgartools

Spec §4.9 and task 2.2, **library-first** per rev 3. edgartools does the fetching, pacing, and item extraction; we own storage and verification. The stored item text becomes the document of record: every `span_id` later resolves to a character range **in the stored text**, which is exact and reproducible (offsets into the original filing HTML are not preserved by the library, and do not need to be — resolvability is to the stored document).

Timebox (spec §14): if edgartools item extraction fails on more than 2 of the 10 companies after ~2 hours of fiddling, stop — fall back to storing each filing's full plain text as a single item `"FULL"` and let Task 7's chunker do the rest. Record which companies fell back.

**Files:**
- Create: `src/edgar/narrative/__init__.py` (empty), `src/edgar/narrative/store.py` (DDL only in this task), `src/edgar/narrative/fetch.py`
- Test: `tests/test_narrative_fetch.py`
- Modify: `Makefile` (target `narrative`)

**Interfaces:**
- Consumes: `Settings.narrative_ciks`, `Settings.sec_user_agent`.
- Produces:

```python
# edgar.narrative.store
NARRATIVE_DDL: str
# narrative_doc(doc_id VARCHAR PK, cik BIGINT, accession VARCHAR, form VARCHAR,
#               filed_date DATE, fiscal_year VARCHAR, item VARCHAR, text VARCHAR)
# span(span_id VARCHAR PK, doc_id VARCHAR, cik BIGINT, accession VARCHAR,
#      form VARCHAR, item VARCHAR, filed_date DATE,
#      char_start INTEGER, char_end INTEGER, text VARCHAR, embedding FLOAT[384])
create_narrative_tables(con) -> None

# edgar.narrative.fetch
ITEMS: tuple[str, ...] = ("Item 1", "Item 1A", "Item 7")
class FilingDoc(NamedTuple):
    cik: int; accession: str; form: str; filed_date: date
    fiscal_year: str; items: dict[str, str]        # item -> plain text
Fetcher = Callable[[int, int], list[FilingDoc]]     # (cik, n_filings) -> docs
fetch_narratives(con, ciks: Sequence[int], per_company: int = 4,
                 fetcher: Fetcher | None = None) -> dict
edgartools_fetcher(cik: int, n_filings: int) -> list[FilingDoc]   # the real one
verify_store(con) -> list[str]     # human-readable problems; [] when clean
```

`doc_id` = `sha256(f"{accession}|{item}")[:16]`. `fetch_narratives` is idempotent (`INSERT OR REPLACE`). Tests use a fake fetcher; `edgartools_fetcher` is exercised only by the manual `make narrative` run.

- [ ] **Step 1: Failing tests** — `tests/test_narrative_fetch.py`:

```python
from datetime import date
from edgar.db import connect
from edgar.narrative.store import create_narrative_tables
from edgar.narrative.fetch import fetch_narratives, FilingDoc, verify_store

def _fake_fetcher(cik, n):
    return [FilingDoc(cik=cik, accession=f"acc-{cik}-{i}", form="10-K",
                      filed_date=date(2024 - i, 10, 1), fiscal_year=str(2024 - i),
                      items={"Item 1": "We sell widgets. " * 60,
                             "Item 1A": "Risks include competition. " * 60,
                             "Item 7": "Revenue grew due to pricing. " * 60})
            for i in range(n)]

def test_fetch_stores_one_row_per_item(tmp_path):
    con = connect(tmp_path / "t.duckdb")
    create_narrative_tables(con)
    report = fetch_narratives(con, [1, 2], per_company=2, fetcher=_fake_fetcher)
    n = con.execute("SELECT count(*) FROM narrative_doc").fetchone()[0]
    assert n == 2 * 2 * 3           # ciks × filings × items
    assert report["docs"] == 12 and report["companies"] == 2
    assert verify_store(con) == []

def test_fetch_is_idempotent(tmp_path):
    con = connect(tmp_path / "t.duckdb")
    create_narrative_tables(con)
    fetch_narratives(con, [1], per_company=1, fetcher=_fake_fetcher)
    fetch_narratives(con, [1], per_company=1, fetcher=_fake_fetcher)
    assert con.execute("SELECT count(*) FROM narrative_doc").fetchone()[0] == 3

def test_verify_flags_short_and_missing_items(tmp_path):
    con = connect(tmp_path / "t.duckdb")
    create_narrative_tables(con)
    def bad_fetcher(cik, n):
        return [FilingDoc(cik=cik, accession="a", form="10-K",
                          filed_date=date(2024, 10, 1), fiscal_year="2024",
                          items={"Item 1": "too short"})]   # missing 1A/7, tiny
    fetch_narratives(con, [1], per_company=1, fetcher=bad_fetcher)
    problems = verify_store(con)
    assert any("short" in p for p in problems)
    assert any("Item 7" in p for p in problems)
```

- [ ] **Step 2: Run to verify failure** — ImportError expected.

- [ ] **Step 3: Implement**

`src/edgar/narrative/store.py`:

```python
import duckdb

NARRATIVE_DDL = """
CREATE TABLE IF NOT EXISTS narrative_doc (
    doc_id VARCHAR PRIMARY KEY,
    cik BIGINT, accession VARCHAR, form VARCHAR, filed_date DATE,
    fiscal_year VARCHAR, item VARCHAR, text VARCHAR
);
CREATE TABLE IF NOT EXISTS span (
    span_id VARCHAR PRIMARY KEY,
    doc_id VARCHAR, cik BIGINT, accession VARCHAR, form VARCHAR,
    item VARCHAR, filed_date DATE,
    char_start INTEGER, char_end INTEGER, text VARCHAR,
    embedding FLOAT[384]
);
"""


def create_narrative_tables(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(NARRATIVE_DDL)
```

`src/edgar/narrative/fetch.py`:

```python
import hashlib
from collections.abc import Callable, Sequence
from datetime import date
from typing import NamedTuple
import duckdb

ITEMS: tuple[str, ...] = ("Item 1", "Item 1A", "Item 7")
_MIN_ITEM_CHARS = 500   # a real Item is pages, not a TOC row


class FilingDoc(NamedTuple):
    cik: int
    accession: str
    form: str
    filed_date: date
    fiscal_year: str
    items: dict[str, str]


Fetcher = Callable[[int, int], list["FilingDoc"]]


def _doc_id(accession: str, item: str) -> str:
    return hashlib.sha256(f"{accession}|{item}".encode()).hexdigest()[:16]


def fetch_narratives(con: duckdb.DuckDBPyConnection, ciks: Sequence[int],
                     per_company: int = 4,
                     fetcher: Fetcher | None = None) -> dict:
    if fetcher is None:
        fetcher = edgartools_fetcher
    docs = 0
    for cik in ciks:
        for filing in fetcher(cik, per_company):
            for item, text in filing.items.items():
                con.execute(
                    "INSERT OR REPLACE INTO narrative_doc VALUES "
                    "(?,?,?,?,?,?,?,?)",
                    [_doc_id(filing.accession, item), filing.cik,
                     filing.accession, filing.form, filing.filed_date,
                     filing.fiscal_year, item, text])
                docs += 1
    return {"companies": len(ciks), "docs": docs}


def verify_store(con: duckdb.DuckDBPyConnection) -> list[str]:
    """Every (accession) should carry all three Items at plausible length.

    This is the exhaustive eyeball pass rev 3 calls for — at ~40 documents,
    verification is enumeration, not sampling. Returns problems; print them.
    """
    problems: list[str] = []
    for accn, item, n in con.execute(
            "SELECT accession, item, length(text) FROM narrative_doc "
            "ORDER BY accession, item").fetchall():
        if n < _MIN_ITEM_CHARS:
            problems.append(f"{accn} {item}: suspiciously short ({n} chars)")
    for (accn,) in con.execute(
            "SELECT DISTINCT accession FROM narrative_doc").fetchall():
        have = {r[0] for r in con.execute(
            "SELECT item FROM narrative_doc WHERE accession = ?",
            [accn]).fetchall()}
        for item in ITEMS:
            if item not in have:
                problems.append(f"{accn}: missing {item}")
    return problems


def edgartools_fetcher(cik: int, n_filings: int) -> list[FilingDoc]:
    """Real fetcher. Import inside the function: edgartools is the optional
    [narrative] extra and this path never runs in tests."""
    from edgar import Company, set_identity          # edgartools package
    from edgar.config import get_settings as _s      # our settings
    set_identity(_s().sec_user_agent)
    out: list[FilingDoc] = []
    filings = Company(cik).get_filings(form="10-K").head(n_filings)
    for f in filings:
        tenk = f.obj()
        items: dict[str, str] = {}
        for item in ITEMS:
            try:
                text = tenk[item]
            except (KeyError, TypeError):
                text = None
            if text:
                items[item] = str(text)
        out.append(FilingDoc(cik=cik, accession=f.accession_no, form="10-K",
                             filed_date=f.filing_date,
                             fiscal_year=str(getattr(f, "fiscal_year", "") or
                                             f.filing_date.year),
                             items=items))
    return out
```

**Name-collision warning for the implementer:** the edgartools package is also imported as `edgar`, which collides with this project's `src/edgar` package on `sys.path`. Because `pythonpath=["src"]` puts our package first, a bare `import edgar` inside the repo resolves to OURS. edgartools must therefore be installed in the venv (`pip install -e ".[narrative]"`) AND imported only inside `edgartools_fetcher`, and `make narrative` must run with the installed package importable as `edgar` — **this will not work as written.** Resolution (do this, it is the honest fix): run the real fetch via a standalone script that puts site-packages first. Add `scripts/fetch_narratives.py`:

```python
"""Run OUTSIDE pythonpath=src so `import edgar` resolves to edgartools.
Usage: venv/bin/python scripts/fetch_narratives.py"""
import sys
sys.path = [p for p in sys.path if not p.endswith("/src")]
from edgar import Company, set_identity                    # edgartools
import duckdb, json, hashlib
sys.path.insert(0, "src")
from edgar.config import get_settings                       # ours — reload trick
# ... (script re-implements the fetch loop calling INSERT OR REPLACE directly)
```

If that dual-import dance proves brittle in practice, rename nothing — instead vendor the fetch into the script using edgartools only (no import of our package; read the DuckDB path and user agent from os.environ / .env directly). The script is glue, not library code; keep `fetch_narratives`+`verify_store` (tested, fetcher-injected) as the library surface.

Makefile:

```make
narrative:
	venv/bin/pip install -q -e ".[narrative]"
	venv/bin/python scripts/fetch_narratives.py
```

- [ ] **Step 4: Run** — `venv/bin/pytest tests/test_narrative_fetch.py -q` → PASS; full suite.

- [ ] **Step 5: Real pull + exhaustive verification** — `make narrative` (network; ~40 filings; minutes). Then:

```bash
venv/bin/python -c "
import duckdb; from edgar.config import get_settings
con = duckdb.connect(str(get_settings().duckdb_path), read_only=True)
con.sql('select cik, count(distinct accession) filings, count(*) items, min(filed_date), max(filed_date) from narrative_doc group by 1 order by 1').show()
import sys; sys.path.insert(0,'src')
from edgar.db import connect as _c
from edgar.narrative.fetch import verify_store
print('\n'.join(verify_store(con)) or 'CLEAN')"
```

Expected: 10 ciks × ~4 filings × 3 items ≈ 120 rows, `CLEAN` (or a short list you then inspect by opening the flagged accessions on sec.gov and hand-fixing / falling back per the timebox rule). Record the outcome in the commit message.

- [ ] **Step 6: Commit**

```bash
git add src/edgar/narrative tests/test_narrative_fetch.py scripts/fetch_narratives.py Makefile
git commit -m "feat(narrative): 10-K item store + edgartools fetcher (library-first)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 7: Chunking, embedding, hybrid search — `search_filings`

Spec §4.9: chunked with character offsets preserved; hybrid retrieval (BM25 + embeddings) filtered by `filed_date <= as_of`. Embeddings come from a local model behind a protocol; tests use a deterministic fake. BM25 uses DuckDB's FTS extension when loadable, and degrades to embedding-only when not (the extension download needs network once; degradation is logged, never silent).

**Files:**
- Create: `src/edgar/narrative/embedder.py`, extend `src/edgar/narrative/store.py`
- Test: `tests/test_narrative_search.py`
- Modify: `Makefile` (target `index`), `scripts/fetch_narratives.py` (call indexing after fetch)

**Interfaces:**
- Consumes: `narrative_doc`/`span` tables, `SpanDTO` (Task 3).
- Produces:

```python
# edgar.narrative.embedder
EMBED_DIM = 384
class Embedder(Protocol):
    def encode(self, texts: list[str]) -> list[list[float]]: ...
class FakeEmbedder:            # deterministic, offline; for tests
    def encode(self, texts): ...
class SentenceTransformerEmbedder:   # lazy-imports sentence_transformers
    def __init__(self, model_name: str = "all-MiniLM-L6-v2"): ...
    def encode(self, texts): ...

# edgar.narrative.store (additions)
chunk_text(text: str, target: int = 1200, overlap: int = 150) -> list[tuple[int, int]]
index_spans(con, embedder: Embedder) -> int          # chunks+embeds all docs; idempotent
try_load_fts(con) -> bool                             # INSTALL/LOAD fts; False on failure
search_spans(con, query: str, cik: int, as_of: date, k: int = 8,
             embedder: Embedder, items: list[str] | None = None) -> list[SpanDTO]
```

`span_id` = `sha256(f"{doc_id}|{char_start}|{char_end}")[:16]`. `chunk_text` splits on paragraph boundaries (`"\n\n"` else sentence-ish fallback), emitting `(start, end)` offsets into the stored item text such that `text[start:end]` reproduces the chunk exactly. Hybrid ranking: reciprocal-rank fusion, `score(s) = Σ 1/(60 + rank)` over the embedding ranking and (when FTS loaded) the BM25 ranking.

- [ ] **Step 1: Failing tests** — `tests/test_narrative_search.py`:

```python
from datetime import date
from edgar.db import connect
from edgar.narrative.store import (
    create_narrative_tables, chunk_text, index_spans, search_spans)
from edgar.narrative.embedder import FakeEmbedder, EMBED_DIM

def _doc(con, doc_id, text, cik=1, accn="a-1", item="Item 7",
         filed=date(2023, 10, 1)):
    con.execute("INSERT INTO narrative_doc VALUES (?,?,?,?,?,?,?,?)",
                [doc_id, cik, accn, "10-K", filed, "2023", item, text])

def test_chunk_offsets_roundtrip():
    text = ("Alpha paragraph. " * 30 + "\n\n") * 5
    chunks = chunk_text(text)
    assert chunks, "no chunks produced"
    for s, e in chunks:
        assert 0 <= s < e <= len(text)
        assert text[s:e].strip()

def test_fake_embedder_is_deterministic_and_sized():
    e = FakeEmbedder()
    a, b = e.encode(["hello"]), e.encode(["hello"])
    assert a == b and len(a[0]) == EMBED_DIM

def test_index_and_search_respects_as_of(tmp_path):
    con = connect(tmp_path / "t.duckdb")
    create_narrative_tables(con)
    _doc(con, "d1", "Freight costs pressured gross margin this year. " * 40,
         filed=date(2023, 10, 1))
    _doc(con, "d2", "Freight costs normalized in the following year. " * 40,
         filed=date(2024, 10, 1))
    n = index_spans(con, FakeEmbedder())
    assert n > 0
    hits = search_spans(con, "freight costs", cik=1,
                        as_of=date(2023, 12, 31), k=5, embedder=FakeEmbedder())
    assert hits and all(h.filed_date <= date(2023, 12, 31) for h in hits)
    accs = {h.accession for h in hits}
    assert accs == {"a-1"}

def test_span_text_matches_offsets(tmp_path):
    con = connect(tmp_path / "t.duckdb")
    create_narrative_tables(con)
    body = "Working capital consumed cash due to inventory build. " * 40
    _doc(con, "d1", body)
    index_spans(con, FakeEmbedder())
    h = search_spans(con, "inventory build", cik=1, as_of=date(2024, 1, 1),
                     k=1, embedder=FakeEmbedder())[0]
    assert body[h.char_start:h.char_end] == h.text

def test_item_filter(tmp_path):
    con = connect(tmp_path / "t.duckdb")
    create_narrative_tables(con)
    _doc(con, "d1", "Risky business risks. " * 40, item="Item 1A")
    _doc(con, "d2", "Discussion of results. " * 40, item="Item 7")
    index_spans(con, FakeEmbedder())
    hits = search_spans(con, "risks", cik=1, as_of=date(2024, 1, 1), k=5,
                        embedder=FakeEmbedder(), items=["Item 1A"])
    assert hits and all(h.item == "Item 1A" for h in hits)

def test_index_is_idempotent(tmp_path):
    con = connect(tmp_path / "t.duckdb")
    create_narrative_tables(con)
    _doc(con, "d1", "Some narrative text here. " * 40)
    index_spans(con, FakeEmbedder())
    n1 = con.execute("SELECT count(*) FROM span").fetchone()[0]
    index_spans(con, FakeEmbedder())
    assert con.execute("SELECT count(*) FROM span").fetchone()[0] == n1
```

- [ ] **Step 2: Run to verify failure** — ImportError expected.

- [ ] **Step 3: Implement**

`src/edgar/narrative/embedder.py`:

```python
import hashlib
import math
from typing import Protocol

EMBED_DIM = 384


class Embedder(Protocol):
    def encode(self, texts: list[str]) -> list[list[float]]: ...


class FakeEmbedder:
    """Deterministic bag-of-token-hash vectors. Offline; similarity is
    token overlap, which is exactly enough to test ranking plumbing."""

    def encode(self, texts: list[str]) -> list[list[float]]:
        out = []
        for t in texts:
            v = [0.0] * EMBED_DIM
            for tok in t.lower().split():
                h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
                v[h % EMBED_DIM] += 1.0
            norm = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / norm for x in v])
        return out


class SentenceTransformerEmbedder:
    """all-MiniLM-L6-v2: 384-dim, local, free. Lazy import — the model
    download (~90MB, one-time) happens only on real indexing runs."""

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        from sentence_transformers import SentenceTransformer
        self._model = SentenceTransformer(model_name)

    def encode(self, texts: list[str]) -> list[list[float]]:
        return [list(map(float, v)) for v in
                self._model.encode(texts, normalize_embeddings=True)]
```

Additions to `src/edgar/narrative/store.py`:

```python
import hashlib
from datetime import date
from edgar.narrative.embedder import Embedder, EMBED_DIM
from edgar.tools.schemas import SpanDTO


def chunk_text(text: str, target: int = 1200, overlap: int = 150
               ) -> list[tuple[int, int]]:
    """Greedy paragraph packer. Offsets index the stored item text;
    text[start:end] IS the span — that identity is what a citation means."""
    breaks = [0]
    i = text.find("\n\n")
    while i != -1:
        breaks.append(i + 2)
        i = text.find("\n\n", i + 2)
    breaks.append(len(text))
    if len(breaks) <= 2 and len(text) > target:     # no paragraphs: fixed windows
        step = target - overlap
        return [(s, min(s + target, len(text)))
                for s in range(0, len(text), step) if text[s:s + target].strip()]
    chunks, start = [], 0
    for b in breaks[1:]:
        if b - start >= target:
            chunks.append((start, b))
            start = max(start, b - overlap)
    if start < len(text) and text[start:].strip():
        chunks.append((start, len(text)))
    return chunks


def try_load_fts(con) -> bool:
    try:
        con.execute("INSTALL fts; LOAD fts;")
        return True
    except Exception:
        return False


def index_spans(con, embedder: Embedder) -> int:
    docs = con.execute(
        "SELECT doc_id, cik, accession, form, item, filed_date, text "
        "FROM narrative_doc").fetchall()
    con.execute("DELETE FROM span")          # rebuild = idempotent by content ids
    n = 0
    for doc_id, cik, accn, form, item, filed, text in docs:
        offsets = chunk_text(text)
        pieces = [text[s:e] for s, e in offsets]
        if not pieces:
            continue
        vecs = embedder.encode(pieces)
        rows = []
        for (s, e), piece, vec in zip(offsets, pieces, vecs):
            span_id = hashlib.sha256(f"{doc_id}|{s}|{e}".encode()).hexdigest()[:16]
            rows.append([span_id, doc_id, cik, accn, form, item, filed,
                         s, e, piece, vec])
        con.executemany(
            "INSERT OR REPLACE INTO span VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
        n += len(rows)
    if try_load_fts(con):
        con.execute("PRAGMA create_fts_index('span', 'span_id', 'text', "
                    "overwrite=1)")
    return n


def search_spans(con, query: str, cik: int, as_of: date, k: int = 8,
                 embedder: Embedder | None = None,
                 items: list[str] | None = None) -> list[SpanDTO]:
    assert embedder is not None
    item_pred = ""
    params: list = []
    if items:
        item_pred = f"AND item IN ({', '.join('?' for _ in items)})"
        params = list(items)
    qvec = embedder.encode([query])[0]
    emb_rows = con.execute(
        f"""SELECT span_id FROM span
            WHERE cik = ? AND filed_date <= ? {item_pred}
            ORDER BY array_cosine_similarity(embedding, ?::FLOAT[{EMBED_DIM}]) DESC
            LIMIT ?""",
        [cik, as_of, *params, qvec, k * 4]).fetchall()
    rankings = [[r[0] for r in emb_rows]]
    if try_load_fts(con):
        try:
            bm_rows = con.execute(
                f"""SELECT span_id FROM (
                        SELECT span_id, fts_main_span.match_bm25(span_id, ?) AS s
                        FROM span WHERE cik = ? AND filed_date <= ? {item_pred})
                    WHERE s IS NOT NULL ORDER BY s DESC LIMIT ?""",
                [query, cik, as_of, *params, k * 4]).fetchall()
            rankings.append([r[0] for r in bm_rows])
        except Exception:
            pass          # index absent on this connection: embedding-only
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, sid in enumerate(ranking):
            scores[sid] = scores.get(sid, 0.0) + 1.0 / (60 + rank)
    top = sorted(scores, key=scores.get, reverse=True)[:k]
    if not top:
        return []
    rows = con.execute(
        f"""SELECT span_id, cik, accession, form, item, filed_date,
                   char_start, char_end, text FROM span
            WHERE span_id IN ({', '.join('?' for _ in top)})""", top).fetchall()
    by_id = {r[0]: r for r in rows}
    return [SpanDTO(span_id=r[0], cik=r[1], accession=r[2], form=r[3],
                    item=r[4], filed_date=r[5], char_start=r[6],
                    char_end=r[7], text=r[8])
            for r in (by_id[sid] for sid in top if sid in by_id)]
```

Makefile target `index` (real store, real model):

```make
index:
	venv/bin/python -c "from edgar.db import connect; from edgar.config import get_settings; from edgar.narrative.store import index_spans; from edgar.narrative.embedder import SentenceTransformerEmbedder; print(index_spans(connect(get_settings().duckdb_path), SentenceTransformerEmbedder()), 'spans indexed')"
```

- [ ] **Step 4: Run** — `venv/bin/pytest tests/test_narrative_search.py -q` → PASS; full suite. (If the FTS `INSTALL` fails offline, tests still pass — the design degrades to embedding-only; confirm no test depends on BM25 specifically.)

- [ ] **Step 5: Index the real store** — `make index` (first run downloads the MiniLM model). Sanity: search AAPL MD&A for a phrase you can see on sec.gov and confirm the hit resolves (`text[char_start:char_end] == text`).

- [ ] **Step 6: Commit**

```bash
git add src/edgar/narrative tests/test_narrative_search.py Makefile
git commit -m "feat(narrative): offset-preserving chunks, hybrid as-of search

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 8: Dated episodic memory

Spec §7.4 rev 3 and task 2.2b. One table for sessions, one for conclusions; recall is a SQL predicate. This is the second temporal-leakage surface and the eval tests it (Task 15).

**Files:**
- Create: `src/edgar/memory/__init__.py` (empty), `src/edgar/memory/episodic.py`
- Test: `tests/test_episodic.py`

**Interfaces:**
- Consumes: nothing new.
- Produces:

```python
MEMORY_DDL: str
# session(session_id VARCHAR PK, cik BIGINT, as_of_date DATE, config_version VARCHAR,
#         trace_id VARCHAR, started_at TIMESTAMP, question VARCHAR,
#         recalled_conclusion_ids VARCHAR)   -- JSON list; the eval reads this
# session_conclusion(conclusion_id VARCHAR PK, session_id VARCHAR, cik BIGINT,
#                    conclusion VARCHAR, learned_as_of DATE, trace_id VARCHAR)
create_memory_tables(con) -> None
@dataclass(frozen=True)
class Conclusion: conclusion_id: str; session_id: str; cik: int
                  conclusion: str; learned_as_of: date; trace_id: str
save_session(con, *, session_id: str, cik: int, as_of: date,
             config_version: str, trace_id: str, question: str | None,
             recalled_conclusion_ids: list[str]) -> None
record_conclusions(con, *, session_id: str, cik: int, conclusions: list[str],
                   learned_as_of: date, trace_id: str) -> list[str]  # ids
recall_conclusions(con, cik: int, as_of: date, limit: int = 5) -> list[Conclusion]
```

`conclusion_id` = `"C-" + sha256(f"{session_id}|{i}|{text}")[:16]`. `learned_as_of` is ALWAYS the producing session's `as_of` — a conclusion derived from 2025-visible data is stamped 2025 even if computed today.

- [ ] **Step 1: Failing tests** — `tests/test_episodic.py`:

```python
from datetime import date
from edgar.db import connect
from edgar.memory.episodic import (
    create_memory_tables, save_session, record_conclusions, recall_conclusions)

def _db(tmp_path):
    con = connect(tmp_path / "t.duckdb")
    create_memory_tables(con)
    return con

def _session(con, sid, as_of, conclusions):
    save_session(con, session_id=sid, cik=1, as_of=as_of, config_version="v1",
                 trace_id=f"tr-{sid}", question=None, recalled_conclusion_ids=[])
    record_conclusions(con, session_id=sid, cik=1, conclusions=conclusions,
                       learned_as_of=as_of, trace_id=f"tr-{sid}")

def test_recall_blocks_later_learned_conclusions(tmp_path):
    con = _db(tmp_path)
    _session(con, "s23", date(2023, 6, 1), ["margins compressed in 2022"])
    _session(con, "s25", date(2025, 6, 1), ["margins recovered by 2025"])
    got = recall_conclusions(con, cik=1, as_of=date(2023, 12, 31))
    assert [c.conclusion for c in got] == ["margins compressed in 2022"]
    got_25 = recall_conclusions(con, cik=1, as_of=date(2025, 12, 31))
    assert len(got_25) == 2
    assert got_25[0].learned_as_of == date(2025, 6, 1)   # recency first

def test_recall_is_cik_scoped_and_limited(tmp_path):
    con = _db(tmp_path)
    for i in range(7):
        _session(con, f"s{i}", date(2023, 1, 1 + i), [f"conclusion {i}"])
    save_session(con, session_id="other", cik=2, as_of=date(2023, 6, 1),
                 config_version="v1", trace_id="t", question=None,
                 recalled_conclusion_ids=[])
    record_conclusions(con, session_id="other", cik=2, conclusions=["theirs"],
                       learned_as_of=date(2023, 6, 1), trace_id="t")
    got = recall_conclusions(con, cik=1, as_of=date(2024, 1, 1), limit=5)
    assert len(got) == 5 and all(c.cik == 1 for c in got)

def test_conclusion_ids_are_stable(tmp_path):
    con = _db(tmp_path)
    ids1 = record_conclusions(con, session_id="s", cik=1, conclusions=["x"],
                              learned_as_of=date(2023, 1, 1), trace_id="t")
    ids2 = record_conclusions(con, session_id="s", cik=1, conclusions=["x"],
                              learned_as_of=date(2023, 1, 1), trace_id="t")
    assert ids1 == ids2
    n = con.execute("SELECT count(*) FROM session_conclusion").fetchone()[0]
    assert n == 1
```

- [ ] **Step 2: Run to verify failure** — ImportError expected.

- [ ] **Step 3: Implement** — `src/edgar/memory/episodic.py`:

```python
import hashlib
import json
from dataclasses import dataclass
from datetime import date
import duckdb

MEMORY_DDL = """
CREATE TABLE IF NOT EXISTS session (
    session_id VARCHAR PRIMARY KEY,
    cik BIGINT, as_of_date DATE, config_version VARCHAR,
    trace_id VARCHAR, started_at TIMESTAMP DEFAULT current_timestamp,
    question VARCHAR, recalled_conclusion_ids VARCHAR
);
CREATE TABLE IF NOT EXISTS session_conclusion (
    conclusion_id VARCHAR PRIMARY KEY,
    session_id VARCHAR, cik BIGINT, conclusion VARCHAR,
    learned_as_of DATE, trace_id VARCHAR
);
"""


def create_memory_tables(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(MEMORY_DDL)


@dataclass(frozen=True)
class Conclusion:
    conclusion_id: str
    session_id: str
    cik: int
    conclusion: str
    learned_as_of: date
    trace_id: str


def save_session(con, *, session_id: str, cik: int, as_of: date,
                 config_version: str, trace_id: str, question: str | None,
                 recalled_conclusion_ids: list[str]) -> None:
    con.execute(
        "INSERT OR REPLACE INTO session "
        "(session_id, cik, as_of_date, config_version, trace_id, question, "
        " recalled_conclusion_ids) VALUES (?,?,?,?,?,?,?)",
        [session_id, cik, as_of, config_version, trace_id, question,
         json.dumps(recalled_conclusion_ids)])


def record_conclusions(con, *, session_id: str, cik: int,
                       conclusions: list[str], learned_as_of: date,
                       trace_id: str) -> list[str]:
    """learned_as_of MUST be the producing session's as_of: a conclusion
    derived from 2025-visible data is a 2025 object even if written today.
    That stamp is the entire leakage guarantee (spec §7.4)."""
    ids = []
    for i, text in enumerate(conclusions):
        cid = "C-" + hashlib.sha256(
            f"{session_id}|{i}|{text}".encode()).hexdigest()[:16]
        con.execute(
            "INSERT OR REPLACE INTO session_conclusion VALUES (?,?,?,?,?,?)",
            [cid, session_id, cik, text, learned_as_of, trace_id])
        ids.append(cid)
    return ids


def recall_conclusions(con, cik: int, as_of: date,
                       limit: int = 5) -> list[Conclusion]:
    rows = con.execute(
        "SELECT conclusion_id, session_id, cik, conclusion, learned_as_of, "
        "trace_id FROM session_conclusion "
        "WHERE cik = ? AND learned_as_of <= ? "
        "ORDER BY learned_as_of DESC, conclusion_id LIMIT ?",
        [cik, as_of, limit]).fetchall()
    return [Conclusion(*r) for r in rows]
```

- [ ] **Step 4: Run** — `venv/bin/pytest tests/test_episodic.py -q` → PASS; full suite.

- [ ] **Step 5: Commit**

```bash
git add src/edgar/memory tests/test_episodic.py
git commit -m "feat(memory): dated episodic store with learned_as_of recall guard

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 9: Tracing — Tracer protocol, Langfuse adapter, compose file

Spec §9.1: instrument during the build, not after. Everything downstream (agent nodes, tools loop, guardrails, eval) emits through this protocol; Langfuse is one adapter behind it, so tests never need the backend and a Langfuse API drift breaks exactly one file.

**Files:**
- Create: `src/edgar/ops/__init__.py` (empty), `src/edgar/ops/tracing.py`, `docker-compose.langfuse.yml` is NOT hand-written — see Step 5.
- Test: `tests/test_tracing.py`
- Modify: `Makefile` (target `langfuse-up`), `.env` (documented placeholder comments only — no secrets)

**Interfaces:**
- Produces:

```python
class Span(Protocol):
    def event(self, name: str, **attrs) -> None: ...
class Tracer(Protocol):
    trace_id: str
    def span(self, name: str, **attrs) -> AbstractContextManager[Span]: ...
    def flush(self) -> None: ...
class NoopTracer:        # default; trace_id = "trace-" + uuid4().hex[:12]
class RecordingTracer:   # test double; .spans: list[(name, attrs)], .events: list[...]
make_tracer(run_name: str) -> Tracer
# LangfuseTracer if LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are set
# (after load_secrets_env()), else NoopTracer. Import langfuse lazily.
```

- [ ] **Step 1: Failing tests** — `tests/test_tracing.py`:

```python
from edgar.ops.tracing import NoopTracer, RecordingTracer, make_tracer

def test_noop_supports_nesting_and_flush():
    t = NoopTracer()
    with t.span("outer", cik=1) as s:
        s.event("tool_call", tool="get_facts")
        with t.span("inner") as s2:
            s2.event("x")
    t.flush()
    assert t.trace_id.startswith("trace-")

def test_recording_tracer_captures_spans_and_events():
    t = RecordingTracer()
    with t.span("retrieve", section="growth") as s:
        s.event("tool_call", tool="compute")
    assert ("retrieve", {"section": "growth"}) in t.spans
    assert t.events == [("retrieve", "tool_call", {"tool": "compute"})]

def test_make_tracer_defaults_to_noop(monkeypatch):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    assert isinstance(make_tracer("memo"), NoopTracer)
```

- [ ] **Step 2: Run to verify failure** — ImportError expected.

- [ ] **Step 3: Implement** — `src/edgar/ops/tracing.py`:

```python
import os
import uuid
from contextlib import contextmanager
from typing import Protocol
from collections.abc import Iterator


class Span(Protocol):
    def event(self, name: str, **attrs) -> None: ...


class Tracer(Protocol):
    trace_id: str
    def span(self, name: str, **attrs): ...
    def flush(self) -> None: ...


class _NoopSpan:
    def event(self, name: str, **attrs) -> None:
        pass


class NoopTracer:
    def __init__(self) -> None:
        self.trace_id = "trace-" + uuid.uuid4().hex[:12]

    @contextmanager
    def span(self, name: str, **attrs) -> Iterator[_NoopSpan]:
        yield _NoopSpan()

    def flush(self) -> None:
        pass


class _RecordingSpan:
    def __init__(self, tracer: "RecordingTracer", name: str) -> None:
        self._tracer, self._name = tracer, name

    def event(self, name: str, **attrs) -> None:
        self._tracer.events.append((self._name, name, attrs))


class RecordingTracer:
    def __init__(self) -> None:
        self.trace_id = "trace-test"
        self.spans: list[tuple[str, dict]] = []
        self.events: list[tuple[str, str, dict]] = []

    @contextmanager
    def span(self, name: str, **attrs):
        self.spans.append((name, attrs))
        yield _RecordingSpan(self, name)

    def flush(self) -> None:
        pass


class LangfuseTracer:
    """Thin adapter over the langfuse SDK (v4, OTEL-based). If the SDK's
    surface differs at implementation time, THIS file is the only one that
    changes — consult https://langfuse.com/docs/sdk/python and keep the
    protocol identical."""

    def __init__(self, run_name: str) -> None:
        from langfuse import Langfuse
        self._lf = Langfuse()             # reads LANGFUSE_* env vars
        self._run_name = run_name
        self.trace_id = "trace-" + uuid.uuid4().hex[:12]

    @contextmanager
    def span(self, name: str, **attrs):
        with self._lf.start_as_current_span(name=name) as lf_span:
            lf_span.update(metadata={**attrs, "run": self._run_name,
                                     "local_trace_id": self.trace_id})

            class _S:
                def event(_self, ev_name: str, **ev_attrs) -> None:
                    lf_span.update(metadata={f"event:{ev_name}": ev_attrs})
            yield _S()

    def flush(self) -> None:
        self._lf.flush()


def make_tracer(run_name: str) -> Tracer:
    if os.environ.get("LANGFUSE_PUBLIC_KEY") and \
            os.environ.get("LANGFUSE_SECRET_KEY"):
        try:
            return LangfuseTracer(run_name)
        except Exception as exc:          # backend down ≠ agent down
            print(f"WARNING: langfuse unavailable ({exc}); tracing disabled")
    return NoopTracer()
```

- [ ] **Step 4: Run** — `venv/bin/pytest tests/test_tracing.py -q` → PASS; full suite.

- [ ] **Step 5: Backend setup (documented, not scripted)** — add to `Makefile`:

```make
langfuse-up:
	@test -d ../langfuse || git clone https://github.com/langfuse/langfuse.git ../langfuse
	cd ../langfuse && docker compose up -d
	@echo "Langfuse at http://localhost:3000 — create an org/project, then put"
	@echo "LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY / LANGFUSE_HOST=http://localhost:3000 in .env"
```

Append to `.env` (comments + empty placeholders only — values are user-supplied and gitignored anyway):

```
# Tracing (optional): populate after `make langfuse-up` + creating a project.
# LANGFUSE_PUBLIC_KEY=
# LANGFUSE_SECRET_KEY=
# LANGFUSE_HOST=http://localhost:3000
# LLM access for agent/eval runs:
# ANTHROPIC_API_KEY=
```

Run `make langfuse-up` once, confirm the UI loads, create keys, verify `make memo` (after Task 14) produces a visible trace. This is a manual checkpoint, not a test.

- [ ] **Step 6: Commit**

```bash
git add src/edgar/ops tests/test_tracing.py Makefile
git commit -m "feat(ops): Tracer protocol with Langfuse adapter and noop fallback

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 10: Procedural memory + versioned agent config

Spec §7.4 (procedural tier) and §9.2 (one versioned object for everything that changes behavior). Prompts are files; the config names them and pins every knob; `config_version` stamps sessions, memos, and eval reports so metric changes are attributable.

**Files:**
- Create: `prompts/system.md`, `prompts/sections/*.md` (11 files), `config/versions/v1.yaml`, `src/edgar/memory/procedural.py`, `src/edgar/agent/__init__.py` (empty), `src/edgar/agent/agent_config.py`
- Test: `tests/test_procedural.py`, `tests/test_agent_config.py`

**Interfaces:**
- Produces:

```python
# edgar.memory.procedural
SECTIONS: tuple[tuple[int, str, str], ...]   # (number, slug, title) — 11 entries
load_system_prompt(prompts_dir: Path | None = None) -> str
load_rubric(slug: str, prompts_dir: Path | None = None) -> str

# edgar.agent.agent_config
class AgentConfig(BaseModel):
    name: str; generation_model: str; judge_model: str
    retrieval_k: int; max_tool_turns: int; max_repair_rounds: int
    context_budget_chars: int; compaction_threshold_chars: int
    recall_limit: int; prompts_sha: str; config_version: str  # e.g. "v1+3fa9c2d1"
load_agent_config(name: str = "v1", root: Path | None = None) -> AgentConfig
```

`SECTIONS` (slugs are also the rubric filenames and the memo section keys — every later task uses these exact slugs):

```python
SECTIONS = (
    (1, "business", "Business description"),
    (2, "growth", "Growth"),
    (3, "profitability", "Profitability"),
    (4, "cash_quality", "Cash quality"),
    (5, "capital_intensity", "Capital intensity"),
    (6, "working_capital", "Working capital"),
    (7, "leverage", "Balance sheet and leverage"),
    (8, "peers", "Peer positioning"),
    (9, "management", "Management's explanation and new risk factors"),
    (10, "reliability", "Data reliability flags"),
    (11, "unanswered", "What could not be answered"),
)
```

`config_version` = `f"{name}+{sha256(yaml_bytes + all prompt bytes)[:8]}"` — any edit to a prompt or knob changes the version string automatically.

- [ ] **Step 1: Failing tests**

`tests/test_procedural.py`:

```python
from edgar.memory.procedural import SECTIONS, load_system_prompt, load_rubric

def test_eleven_sections_with_stable_slugs():
    assert len(SECTIONS) == 11
    assert [s[1] for s in SECTIONS][:3] == ["business", "growth", "profitability"]

def test_every_section_has_a_nonempty_rubric():
    for _, slug, _ in SECTIONS:
        text = load_rubric(slug)
        assert len(text) > 100, f"rubric {slug} too thin"

def test_system_prompt_states_the_three_laws():
    text = load_system_prompt()
    for needle in ("cite", "as_of", "compute"):
        assert needle in text.lower()
```

`tests/test_agent_config.py`:

```python
from edgar.agent.agent_config import load_agent_config

def test_v1_loads_with_pinned_models():
    cfg = load_agent_config("v1")
    assert cfg.generation_model == "claude-opus-5"
    assert cfg.judge_model == "claude-sonnet-5"
    assert cfg.config_version.startswith("v1+") and len(cfg.config_version) == 11

def test_version_hash_moves_when_prompts_change(tmp_path):
    import shutil
    from pathlib import Path
    root = tmp_path
    shutil.copytree(Path("config"), root / "config")
    shutil.copytree(Path("prompts"), root / "prompts")
    v_before = load_agent_config("v1", root=root).config_version
    (root / "prompts" / "system.md").write_text("changed\n" * 20)
    assert load_agent_config("v1", root=root).config_version != v_before
```

- [ ] **Step 2: Run to verify failure** — ImportError / missing files expected.

- [ ] **Step 3: Implement**

`config/versions/v1.yaml`:

```yaml
generation_model: claude-opus-5
judge_model: claude-sonnet-5
retrieval_k: 8
max_tool_turns: 12          # per section; hard stop on the retrieve loop
max_repair_rounds: 2        # guardrail repair attempts before downgrade-all
context_budget_chars: 60000 # ledger render budget fed to the memo writer
compaction_threshold_chars: 45000
recall_limit: 5             # episodic conclusions loaded per run
```

`prompts/system.md` — the agent's constitution, verbatim:

```markdown
# Diligence analyst — operating rules

You are a financial diligence analyst writing about ONE public company as
of a FIXED cutoff date. You work only from tool results. The tools enforce
the cutoff in the database; you will simply never see later information.

## The three laws

1. **Every numeric or attributed statement carries a citation.** A citation
   is an identifier copied VERBATIM from a tool result: a `fact_id`, a
   `span_id`, or a `derivation_id`. Never invent, abbreviate, or repair an
   identifier. A statement you cannot cite is a hypothesis and must be
   labeled as one.
2. **You do no arithmetic.** Every growth rate, margin, ratio, delta, and
   difference — however trivial — goes through the `compute` tool over
   fact_ids, and you cite the returned derivation_id. This includes percent
   changes you could do in your head.
3. **What the tools cannot show does not exist.** When the coverage map
   reports a field as NOT_DISCLOSED, NOT_YET_FILED, UNMAPPED, or AMBIGUOUS,
   report that status code. Never estimate, interpolate, or fill from
   general knowledge. "I cannot answer this from the store" is a correct
   and expected answer.

## Style

Terse and factual. No superlatives, no filler. One assertion per claim.
Prefer exact figures with units over rounded prose. Distinguish what the
data shows from what management says (attributed) and from what you infer
(inferential — cite the premises). Value-creation ideas go in the
hypotheses list, labeled, never asserted as fact.
```

`prompts/sections/growth.md` — the pattern every rubric follows, verbatim:

```markdown
# Section: Growth

Question: is revenue growing, and is growth accelerating or decelerating?

Fields: `revenue` (duration). Pull 8-12 quarters via `get_facts`.

Computations (all via `compute`, citing derivation_ids):
- YoY growth for the latest 4 quarters: `(rev_t - rev_t4) / rev_t4`
- Sequential trend: compare consecutive YoY rates; state whether the
  growth rate is rising or falling — cite both derivations.

Watch for: fiscal-year boundaries (use period_start/period_end, not labels);
a revenue restatement visible as multiple filed versions of one period —
if present, mention it and let section 10 elaborate.

If revenue is missing or AMBIGUOUS for recent periods: report the status
code and write what can be said from older periods, or set the section to
status_code if nothing is producible.
```

`prompts/sections/working_capital.md` — verbatim (the days-metrics section that needs Task 1's fields):

```markdown
# Section: Working capital

Question: how much cash is trapped in the operating cycle, and is the
trend improving or worsening?

Fields: `inventory`, `accounts_receivable`, `accounts_payable` (instants);
`revenue`, `cost_of_revenue` (durations). Latest 4-8 quarters.

Computations (via `compute`; instant/duration ratios are expected here):
- Days inventory:  `inventory / cost_of_revenue * 91`
- Days receivable: `accounts_receivable / revenue * 91`
- Days payable:    `accounts_payable / cost_of_revenue * 91`
Use quarter-aligned inputs (same period_end for the instant and the
quarter the duration covers). Compute for at least two periods and state
the direction of travel, citing every derivation.

If `cost_of_revenue` is missing (common: ~half of filers), say so with its
status code and compute only days receivable; do not substitute revenue
into the inventory/payable formulas.
```

The remaining nine rubrics follow the growth.md pattern exactly — question, fields (exact canonical names), computations (exact `compute` expressions), missing-data instruction. Their content specs: `business.md` (search_filings Item 1, attributed claims only, no numbers without facts); `profitability.md` (gross/operating/net margins via compute from `gross_profit`,`operating_income`,`net_income`,`revenue`; where in the stack pressure sits); `cash_quality.md` (net_income vs operating_cash_flow divergence, FCF = `operating_cash_flow - capex`); `capital_intensity.md` (`capex / revenue`, asset turnover `revenue / total_assets`); `leverage.md` (`long_term_debt / stockholders_equity`, net debt `long_term_debt - cash_and_equivalents`; note the long_term_debt naming caveat from the data dictionary); `peers.md` (get_peer_set, compare 1-2 computed ratios across peers, calendar-aligned only); `management.md` (search_filings Items 7 and 1A, attributed claims with span_ids; new-vs-prior risk factors only if spans from two filings support it); `reliability.md` (restatement evidence: multiple filed versions of one figure surfaced in get_facts results; filing-lag observations; cite fact_ids of both versions); `unanswered.md` (enumerate every non-AVAILABLE status encountered this run with its code; this section is never empty if any other section hit a gap).

`prompts/sections/<slug>.md` — one per section; each states: what question the section answers (plain language), which canonical fields / tools to use (exact field names from Task 1's 15), which computations to run via `compute` (e.g. `working_capital.md`: days inventory = `inventory / cost_of_revenue * 91` on aligned quarters; days receivable = `accounts_receivable / revenue * 91`; days payable = `accounts_payable / cost_of_revenue * 91`; note the instant/duration ratio is expected), and what to do when inputs are missing (emit the status code, move on). `reliability.md` instructs calling `get_facts` at two different `as_of` dates is NOT available — instead cite `restatement` evidence via the fact table's multiple filed versions surfaced in tool results. `unanswered.md` instructs summarizing every non-AVAILABLE status encountered.

`src/edgar/memory/procedural.py`:

```python
from pathlib import Path

SECTIONS: tuple[tuple[int, str, str], ...] = (
    (1, "business", "Business description"),
    (2, "growth", "Growth"),
    (3, "profitability", "Profitability"),
    (4, "cash_quality", "Cash quality"),
    (5, "capital_intensity", "Capital intensity"),
    (6, "working_capital", "Working capital"),
    (7, "leverage", "Balance sheet and leverage"),
    (8, "peers", "Peer positioning"),
    (9, "management", "Management's explanation and new risk factors"),
    (10, "reliability", "Data reliability flags"),
    (11, "unanswered", "What could not be answered"),
)

_DEFAULT = Path("prompts")


def load_system_prompt(prompts_dir: Path | None = None) -> str:
    return ((prompts_dir or _DEFAULT) / "system.md").read_text()


def load_rubric(slug: str, prompts_dir: Path | None = None) -> str:
    if slug not in {s[1] for s in SECTIONS}:
        raise KeyError(f"unknown section slug: {slug}")
    return ((prompts_dir or _DEFAULT) / "sections" / f"{slug}.md").read_text()
```

`src/edgar/agent/agent_config.py`:

```python
import hashlib
from pathlib import Path
import yaml
from pydantic import BaseModel, ConfigDict


class AgentConfig(BaseModel):
    model_config = ConfigDict(frozen=True)
    name: str
    generation_model: str
    judge_model: str
    retrieval_k: int
    max_tool_turns: int
    max_repair_rounds: int
    context_budget_chars: int
    compaction_threshold_chars: int
    recall_limit: int
    prompts_sha: str
    config_version: str


def load_agent_config(name: str = "v1", root: Path | None = None) -> AgentConfig:
    root = root or Path(".")
    yaml_path = root / "config" / "versions" / f"{name}.yaml"
    raw = yaml_path.read_bytes()
    data = yaml.safe_load(raw)
    h = hashlib.sha256(raw)
    for p in sorted((root / "prompts").rglob("*.md")):
        h.update(p.read_bytes())
    digest = h.hexdigest()[:8]
    return AgentConfig(name=name, prompts_sha=digest,
                       config_version=f"{name}+{digest}", **data)
```

- [ ] **Step 4: Run** — both test files then full suite → PASS.

- [ ] **Step 5: Commit**

```bash
git add prompts config src/edgar/memory/procedural.py src/edgar/agent \
  tests/test_procedural.py tests/test_agent_config.py
git commit -m "feat(agent): procedural rubrics + hash-versioned config (spec 9.2)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 11: Evidence ledger + compaction

Spec §7.1–7.2. The ledger is the typed working memory; the memo is written FROM it. Compaction compresses prose and NEVER drops an identifier — the inviolable rule gets its own test.

**Files:**
- Create: `src/edgar/agent/ledger.py`
- Test: `tests/test_ledger.py`

**Interfaces:**

```python
@dataclass
class LedgerEntry:
    kind: str            # "fact" | "span" | "derivation" | "coverage" | "note"
    identifier: str      # fact_id / span_id / derivation_id / "" for notes
    gist: str            # what this establishes, agent-authored, one line
    section: str         # section slug it serves
    payload: str = ""    # raw tool JSON; dropped first under pressure

class EvidenceLedger:
    entries: list[LedgerEntry]
    def append(self, entry: LedgerEntry) -> None
    def identifiers(self) -> set[str]
    def size_chars(self) -> int
    def compact(self) -> int          # returns chars freed; NEVER drops identifiers
    def render(self, section: str | None = None) -> str   # prompt-ready block
```

`compact()` policy, in order: (1) drop all `payload`s; (2) truncate every `gist` to 200 chars. Identifiers and entry rows are never removed. `render()` emits one line per entry: `[{identifier}] ({kind}, §{section}) {gist}` — the identifier leads so the writer model copies it correctly.

- [ ] **Step 1: Failing tests** — `tests/test_ledger.py`:

```python
from edgar.agent.ledger import EvidenceLedger, LedgerEntry

def _big_ledger(n=50):
    led = EvidenceLedger()
    for i in range(n):
        led.append(LedgerEntry(kind="fact", identifier=f"f{i:04d}",
                               gist=("revenue grew strongly " * 30),
                               section="growth",
                               payload="x" * 2000))
    return led

def test_compaction_never_drops_identifiers():
    led = _big_ledger()
    before = led.identifiers()
    freed = led.compact()
    assert freed > 0
    assert led.identifiers() == before          # THE inviolable rule (spec §7.2)
    assert led.size_chars() < 50 * 2000

def test_compaction_truncates_gists_and_drops_payloads():
    led = _big_ledger()
    led.compact()
    assert all(e.payload == "" for e in led.entries)
    assert all(len(e.gist) <= 200 for e in led.entries)

def test_render_leads_with_identifier_and_filters_by_section():
    led = EvidenceLedger()
    led.append(LedgerEntry("fact", "fA", "rev FY23", "growth"))
    led.append(LedgerEntry("span", "sB", "mgmt on freight", "management"))
    out = led.render(section="growth")
    assert out.splitlines() == ["[fA] (fact, §growth) rev FY23"]
    assert "[sB]" in led.render()

def test_size_counts_gist_and_payload():
    led = EvidenceLedger()
    led.append(LedgerEntry("note", "", "abc", "growth", payload="12345"))
    assert led.size_chars() >= 8
```

- [ ] **Step 2: Run to verify failure** — ImportError expected.

- [ ] **Step 3: Implement** — `src/edgar/agent/ledger.py`:

```python
from dataclasses import dataclass, field

_GIST_CAP = 200


@dataclass
class LedgerEntry:
    kind: str
    identifier: str
    gist: str
    section: str
    payload: str = ""


@dataclass
class EvidenceLedger:
    """Typed working memory (spec §7.1). The memo is written FROM this,
    which is what makes post-hoc auditing possible."""
    entries: list[LedgerEntry] = field(default_factory=list)

    def append(self, entry: LedgerEntry) -> None:
        self.entries.append(entry)

    def identifiers(self) -> set[str]:
        return {e.identifier for e in self.entries if e.identifier}

    def size_chars(self) -> int:
        return sum(len(e.gist) + len(e.payload) for e in self.entries)

    def compact(self) -> int:
        """Compress prose; NEVER compress identifiers (spec §7.2).
        A compaction that drops a citation is a bug, not a tradeoff."""
        before = self.size_chars()
        for e in self.entries:
            e.payload = ""
            if len(e.gist) > _GIST_CAP:
                e.gist = e.gist[:_GIST_CAP - 1] + "…"
        return before - self.size_chars()

    def render(self, section: str | None = None) -> str:
        rows = [e for e in self.entries
                if section is None or e.section == section]
        return "\n".join(
            f"[{e.identifier}] ({e.kind}, §{e.section}) {e.gist}"
            for e in rows)
```

- [ ] **Step 4: Run** — `venv/bin/pytest tests/test_ledger.py -q` → PASS; full suite.

- [ ] **Step 5: Commit**

```bash
git add src/edgar/agent/ledger.py tests/test_ledger.py
git commit -m "feat(agent): evidence ledger; compaction preserves identifiers verbatim

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

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

### Task 13: LLM adapter — `LLMClient` protocol, `AnthropicLLM`, `FakeLLM`

The seam that keeps every agent and eval test offline. Two operations cover everything Stage 2 needs: a tool-use turn and a schema-validated structured parse.

**Files:**
- Create: `src/edgar/agent/llm.py`
- Test: `tests/test_llm.py`

**Interfaces:**

```python
@dataclass(frozen=True)
class ToolCall: id: str; name: str; input: dict
@dataclass(frozen=True)
class LLMTurn:
    text: str                      # concatenated text blocks
    tool_calls: list[ToolCall]     # empty when the model is done
    raw_content: object            # provider content to echo back verbatim
    usage_in: int; usage_out: int

class LLMClient(Protocol):
    def tool_turn(self, *, system: str, messages: list[dict],
                  tools: list[dict]) -> LLMTurn: ...
    def parse_structured(self, *, system: str, prompt: str,
                         output_model: type[BaseModel]) -> BaseModel: ...

class AnthropicLLM:                # real client
    def __init__(self, model: str, max_tokens: int = 16000): ...

class FakeLLM:                     # scripted; raises when script exhausted
    def __init__(self, turns: list[LLMTurn] = (),
                 parsed: list[BaseModel] = ()): ...
```

`AnthropicLLM` rules (from the claude-api reference, current as of 2026-08): `anthropic.Anthropic()` resolves credentials from env; **omit the `thinking` parameter entirely** (Opus 5 / Sonnet 5 default to adaptive; `budget_tokens` returns 400); no assistant prefill; `tool_turn` uses `client.messages.create(model=…, max_tokens=…, system=…, tools=…, messages=…)` and maps `response.content` blocks (`block.type == "text"` → text, `== "tool_use"` → `ToolCall(block.id, block.name, block.input)`); `parse_structured` uses `client.messages.parse(model=…, max_tokens=…, system=…, messages=[{"role":"user","content":prompt}], output_format=output_model)` and returns `response.parsed_output`. Wrap provider errors: catch `anthropic.APIStatusError`/`APIConnectionError` and re-raise as `LLMError(RuntimeError)` with the message — callers never import the anthropic package.

- [ ] **Step 1: Failing tests** — `tests/test_llm.py` (FakeLLM only; AnthropicLLM is exercised by real runs):

```python
import pytest
from pydantic import BaseModel
from edgar.agent.llm import FakeLLM, LLMTurn, ToolCall

class _Out(BaseModel):
    answer: str

def test_fake_llm_plays_script_in_order():
    t1 = LLMTurn(text="", tool_calls=[ToolCall("t1", "get_facts", {"cik": 1})],
                 raw_content=[], usage_in=10, usage_out=5)
    t2 = LLMTurn(text="done", tool_calls=[], raw_content=[], usage_in=8,
                 usage_out=4)
    llm = FakeLLM(turns=[t1, t2], parsed=[_Out(answer="hi")])
    assert llm.tool_turn(system="s", messages=[], tools=[]).tool_calls[0].name \
        == "get_facts"
    assert llm.tool_turn(system="s", messages=[], tools=[]).text == "done"
    assert llm.parse_structured(system="s", prompt="p",
                                output_model=_Out).answer == "hi"

def test_fake_llm_raises_when_script_exhausted():
    llm = FakeLLM()
    with pytest.raises(AssertionError, match="script exhausted"):
        llm.tool_turn(system="s", messages=[], tools=[])

def test_fake_llm_records_calls_for_assertions():
    llm = FakeLLM(turns=[LLMTurn("x", [], [], 1, 1)])
    llm.tool_turn(system="SYS", messages=[{"role": "user", "content": "u"}],
                  tools=[{"name": "compute"}])
    assert llm.calls[0]["system"] == "SYS"
    assert llm.calls[0]["tools"][0]["name"] == "compute"
```

- [ ] **Step 2: Run to verify failure** — ImportError expected.

- [ ] **Step 3: Implement** — `src/edgar/agent/llm.py`:

```python
from dataclasses import dataclass, field
from typing import Protocol
from pydantic import BaseModel


class LLMError(RuntimeError):
    pass


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    input: dict


@dataclass(frozen=True)
class LLMTurn:
    text: str
    tool_calls: list[ToolCall]
    raw_content: object
    usage_in: int
    usage_out: int


class LLMClient(Protocol):
    def tool_turn(self, *, system: str, messages: list[dict],
                  tools: list[dict]) -> LLMTurn: ...
    def parse_structured(self, *, system: str, prompt: str,
                         output_model: type[BaseModel]) -> BaseModel: ...


class AnthropicLLM:
    """Thin provider adapter. Thinking parameter deliberately omitted:
    claude-opus-5 / claude-sonnet-5 run adaptive thinking by default and
    reject budget_tokens with a 400."""

    def __init__(self, model: str, max_tokens: int = 16000):
        import anthropic
        self._anthropic = anthropic
        self._client = anthropic.Anthropic()
        self._model = model
        self._max_tokens = max_tokens

    def tool_turn(self, *, system, messages, tools) -> LLMTurn:
        try:
            resp = self._client.messages.create(
                model=self._model, max_tokens=self._max_tokens,
                system=system, tools=tools, messages=messages)
        except (self._anthropic.APIStatusError,
                self._anthropic.APIConnectionError) as exc:
            raise LLMError(str(exc)) from exc
        text = "".join(b.text for b in resp.content if b.type == "text")
        calls = [ToolCall(b.id, b.name, dict(b.input))
                 for b in resp.content if b.type == "tool_use"]
        return LLMTurn(text=text, tool_calls=calls, raw_content=resp.content,
                       usage_in=resp.usage.input_tokens,
                       usage_out=resp.usage.output_tokens)

    def parse_structured(self, *, system, prompt, output_model):
        try:
            resp = self._client.messages.parse(
                model=self._model, max_tokens=self._max_tokens, system=system,
                messages=[{"role": "user", "content": prompt}],
                output_format=output_model)
        except (self._anthropic.APIStatusError,
                self._anthropic.APIConnectionError) as exc:
            raise LLMError(str(exc)) from exc
        return resp.parsed_output


@dataclass
class FakeLLM:
    """Scripted double. Also records every call for assertions."""
    turns: list[LLMTurn] = field(default_factory=list)
    parsed: list[BaseModel] = field(default_factory=list)
    calls: list[dict] = field(default_factory=list)
    parse_calls: list[dict] = field(default_factory=list)

    def tool_turn(self, *, system, messages, tools) -> LLMTurn:
        self.calls.append({"system": system, "messages": messages,
                           "tools": tools})
        assert self.turns, "FakeLLM script exhausted (tool_turn)"
        return self.turns.pop(0)

    def parse_structured(self, *, system, prompt, output_model):
        self.parse_calls.append({"system": system, "prompt": prompt,
                                 "output_model": output_model})
        assert self.parsed, "FakeLLM script exhausted (parse_structured)"
        out = self.parsed.pop(0)
        assert isinstance(out, output_model), \
            f"scripted {type(out).__name__} != requested {output_model.__name__}"
        return out
```

- [ ] **Step 4: Run** — `venv/bin/pytest tests/test_llm.py -q` → PASS; full suite.

- [ ] **Step 5: Commit**

```bash
git add src/edgar/agent/llm.py tests/test_llm.py
git commit -m "feat(agent): LLMClient protocol with Anthropic adapter and scripted fake

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 14: Agent nodes, LangGraph wiring, CLI

Spec §7 flow: load memory → coverage → plan → per-section retrieve → ledger → compact-if-over-budget → write → guardrail/repair → emit → persist session+conclusions. Nodes are plain functions over a state dict, unit-tested with `FakeLLM` and `RecordingTracer`; LangGraph only sequences them, so a framework drift breaks one thin file.

**Files:**
- Create: `src/edgar/agent/tool_defs.py`, `src/edgar/agent/nodes.py`, `src/edgar/agent/graph.py`, `src/edgar/agent/run.py`
- Test: `tests/test_agent_nodes.py`, `tests/test_agent_graph.py`
- Modify: `Makefile` (target `memo`)

**Interfaces:**

```python
# edgar.agent.tool_defs — anthropic-format tool schemas + dispatcher
TOOL_DEFS: list[dict]        # 5 entries: get_facts, search_filings, compute,
                             # get_peer_set, list_available_facts. input_schema
                             # WITHOUT as_of — the harness injects it; the model
                             # cannot choose a different date (spec §6).
dispatch_tool(con, name: str, args: dict, *, as_of: date,
              embedder, retrieval_k: int) -> tuple[str, list[LedgerEntry]]
    # returns (json_result_for_model, ledger_entries_to_append)
    # tool errors (ComputeError, ValueError) come back as
    # ('{"error": "..."}', []) — the model sees the message and retries;
    # the harness never crashes on a bad model-supplied argument.

# edgar.agent.nodes — every node: (state: AgentState) -> AgentState
AgentState = TypedDict("AgentState", {...})   # cik, as_of, question, config,
    # con-factory, llm, embedder, tracer, ledger, coverage, plan (slugs),
    # section_idx, memo, guardrail_report, repair_round, conclusions,
    # recalled_ids, usage: dict, company_name
load_memory(state) -> state      # recall_conclusions + note in ledger + recalled_ids
coverage_node(state) -> state    # list_available_facts → ledger 'coverage' entries
plan_node(state) -> state        # section slugs to run: all 11 for memo mode;
                                 # ["qa"] for question mode
retrieve_section(state) -> state # manual tool loop (≤ max_tool_turns), ledger
compact_node(state) -> state     # ledger.compact() when over threshold
write_memo(state) -> state       # parse_structured → Memo (from ledger render)
guardrail_node(state) -> state   # check_memo -> guardrail_report
repair_node(state) -> state      # repair_memo(); repair_round += 1
emit(state) -> state             # render_markdown → file; save_session;
                                 # record_conclusions(learned_as_of=as_of)

# edgar.agent.graph
build_graph() -> compiled LangGraph app
run_agent(*, cik, as_of, question=None, config=None, llm=None, embedder=None,
          tracer=None, con=None, out_dir=Path("data/memos")) -> Memo
    # convenience wrapper: builds state, invokes graph, returns final memo.
    # Defaults construct real deps (AnthropicLLM etc.); tests pass fakes.
```

The retrieve loop inside `retrieve_section` (manual loop per the claude-api reference — chosen over the SDK tool runner because every call must be intercepted for ledger writes, span events, and as_of injection):

```python
messages = [{"role": "user", "content": rubric_plus_context}]
for _ in range(cfg.max_tool_turns):
    turn = llm.tool_turn(system=system_prompt, messages=messages,
                         tools=TOOL_DEFS)
    if not turn.tool_calls:
        break
    messages.append({"role": "assistant", "content": turn.raw_content})
    results = []
    for call in turn.tool_calls:
        span.event("tool_call", tool=call.name)
        payload, entries = dispatch_tool(con, call.name, call.input,
                                         as_of=state["as_of"], ...)
        for e in entries:
            e.section = slug
            ledger.append(e)
        results.append({"type": "tool_result", "tool_use_id": call.id,
                        "content": payload})
    messages.append({"role": "user", "content": results})
```

All tool_result blocks for one assistant turn go back in a SINGLE user message (splitting them silently degrades parallel tool use). The final `turn.text` becomes the section's draft note appended to the ledger as a `note` entry.

`write_memo` prompt = system prompt + full `ledger.render()` + the 11 section titles/slugs + instruction: cite ONLY identifiers present in the ledger, one claim per assertion, set `status="status_code"` with the status note for sections whose ledger evidence is only coverage codes. Output model: `Memo` (Task 12) — `parse_structured(output_model=Memo)` gives schema enforcement for free.

`emit` writes `data/memos/{cik}_{as_of}_{config_version}.md` + `.json` (memo.model_dump_json), saves the session row (recalled ids included), records conclusions: the LLM turn inside `emit` is skipped in v1 — conclusions are the claim texts of the 3 sections with most claims? No: keep deterministic and honest — conclusions = every claim text from sections `growth`, `profitability`, `cash_quality` that survived guardrails, capped at 5. Deterministic, testable, no extra LLM call.

- [ ] **Step 1: Failing tests**

`tests/test_agent_nodes.py` — the load-bearing behaviors, all offline:

```python
from datetime import date
from pydantic import TypeAdapter
from edgar.db import connect, init_schema
from edgar.curate.facts import create_fact_table
from edgar.tools.compute import create_derivation_table
from edgar.narrative.store import create_narrative_tables
from edgar.memory.episodic import create_memory_tables, record_conclusions, save_session
from edgar.agent.llm import FakeLLM, LLMTurn, ToolCall
from edgar.agent.ledger import EvidenceLedger
from edgar.agent.memo import Memo, MemoSection, Claim
from edgar.agent.tool_defs import TOOL_DEFS, dispatch_tool
from edgar.agent import nodes
from edgar.narrative.embedder import FakeEmbedder
from edgar.ops.tracing import RecordingTracer
from edgar.agent.agent_config import load_agent_config
# ... _fact helper ...

def _con(tmp_path):
    con = connect(tmp_path / "t.duckdb")
    init_schema(con); create_fact_table(con); create_derivation_table(con)
    create_narrative_tables(con); create_memory_tables(con)
    con.execute("""CREATE TABLE company (cik BIGINT, name VARCHAR, sic VARCHAR,
        sector VARCHAR, fiscal_year_end_month INTEGER, first_filing_date DATE,
        eligibility_status VARCHAR, exclusion_reason VARCHAR)""")
    con.execute("INSERT INTO company VALUES (1,'ACME','3571','manufacturing',"
                "12,DATE '2019-01-01','eligible',NULL)")
    return con

def _state(con, **over):
    st = dict(cik=1, as_of=date(2023, 6, 1), question=None,
              config=load_agent_config("v1"), con=con,
              llm=FakeLLM(), embedder=FakeEmbedder(),
              tracer=RecordingTracer(), ledger=EvidenceLedger(),
              coverage=None, plan=[], section_idx=0, memo=None,
              guardrail_report=None, repair_round=0, conclusions=[],
              recalled_ids=[], usage={"in": 0, "out": 0},
              company_name="ACME")
    st.update(over)
    return st

def test_tool_defs_exclude_as_of_everywhere():
    for t in TOOL_DEFS:
        assert "as_of" not in t["input_schema"].get("properties", {}), t["name"]

def test_dispatch_injects_as_of_and_builds_ledger_entries(tmp_path):
    con = _con(tmp_path)
    _fact(con, fact_id="fA", filed=date(2023, 5, 1))
    payload, entries = dispatch_tool(
        con, "get_facts",
        {"cik": 1, "fields": ["revenue"], "period_start": "2023-01-01",
         "period_end": "2023-03-31"},
        as_of=date(2023, 6, 1), embedder=FakeEmbedder(), retrieval_k=4)
    assert "fA" in payload
    assert entries[0].kind == "fact" and entries[0].identifier == "fA"

def test_dispatch_tool_error_is_payload_not_crash(tmp_path):
    con = _con(tmp_path)
    payload, entries = dispatch_tool(
        con, "compute", {"expression": "a +", "inputs": {}},
        as_of=date(2023, 6, 1), embedder=FakeEmbedder(), retrieval_k=4)
    assert "error" in payload and entries == []

def test_load_memory_respects_as_of(tmp_path):
    con = _con(tmp_path)
    save_session(con, session_id="s25", cik=1, as_of=date(2025, 1, 1),
                 config_version="v1", trace_id="t", question=None,
                 recalled_conclusion_ids=[])
    record_conclusions(con, session_id="s25", cik=1,
                       conclusions=["from the future"],
                       learned_as_of=date(2025, 1, 1), trace_id="t")
    st = nodes.load_memory(_state(con))
    assert st["recalled_ids"] == []
    assert "from the future" not in st["ledger"].render()

def test_retrieve_section_appends_ledger_and_stops_on_no_tools(tmp_path):
    con = _con(tmp_path)
    _fact(con, fact_id="fA", filed=date(2023, 5, 1))
    llm = FakeLLM(turns=[
        LLMTurn(text="", raw_content=[], usage_in=5, usage_out=5,
                tool_calls=[ToolCall("t1", "get_facts",
                                     {"cik": 1, "fields": ["revenue"],
                                      "period_start": "2023-01-01",
                                      "period_end": "2023-03-31"})]),
        LLMTurn(text="Revenue established.", raw_content=[], tool_calls=[],
                usage_in=5, usage_out=5)])
    st = _state(con, llm=llm, plan=["growth"], section_idx=0)
    st = nodes.retrieve_section(st)
    assert "fA" in st["ledger"].identifiers()
    assert st["section_idx"] == 1
    assert any(e.kind == "note" for e in st["ledger"].entries)

def test_write_then_guardrail_then_repair_loop(tmp_path):
    con = _con(tmp_path)
    bad = Memo(cik=1, company_name="ACME", as_of=date(2023, 6, 1),
               sections=[MemoSection(slug="growth", title="Growth",
                                     claims=[Claim(text="Revenue was 100")])])
    llm = FakeLLM(parsed=[bad])
    st = _state(con, llm=llm)
    st = nodes.write_memo(st)
    st = nodes.guardrail_node(st)
    assert st["guardrail_report"].rejection_count == 1
    st = nodes.repair_node(st)
    assert st["memo"].sections[0].claims[0].is_hypothesis

def test_emit_persists_session_and_dated_conclusions(tmp_path):
    con = _con(tmp_path)
    memo = Memo(cik=1, company_name="ACME", as_of=date(2023, 6, 1),
                sections=[MemoSection(slug="growth", title="Growth",
                                      claims=[Claim(text="Revenue was 100",
                                                    citations=["fA"])])],
                config_version="v1+deadbeef", trace_id="tr", session_id="S1")
    st = _state(con, memo=memo)
    st["out_dir"] = tmp_path / "memos"
    nodes.emit(st)
    row = con.execute("SELECT cik, as_of_date FROM session").fetchone()
    assert row == (1, date(2023, 6, 1))
    c = con.execute("SELECT conclusion, learned_as_of "
                    "FROM session_conclusion").fetchone()
    assert c == ("Revenue was 100", date(2023, 6, 1))
    assert (tmp_path / "memos").glob("*.md")
```

`tests/test_agent_graph.py` — one end-to-end offline run:

```python
from datetime import date
from edgar.agent.graph import run_agent
from edgar.agent.llm import FakeLLM, LLMTurn, ToolCall
from edgar.agent.memo import Memo, MemoSection, Claim
# ... same _con helper as test_agent_nodes (copy it) ...

def test_full_run_with_fakes_produces_cited_memo(tmp_path):
    con = _con(tmp_path)
    _fact(con, fact_id="fA", filed=date(2023, 5, 1))
    # One retrieve turn per planned section (script: tool call then done),
    # then a structured memo whose only citation is the ledgered fact.
    turns = []
    for _ in range(11):
        turns += [LLMTurn("", [ToolCall("t", "get_facts",
                                        {"cik": 1, "fields": ["revenue"],
                                         "period_start": "2023-01-01",
                                         "period_end": "2023-03-31"})],
                          [], 1, 1),
                  LLMTurn("noted", [], [], 1, 1)]
    memo = Memo(cik=1, company_name="ACME", as_of=date(2023, 6, 1),
                sections=[MemoSection(slug="growth", title="Growth",
                                      claims=[Claim(text="Revenue was 100",
                                                    citations=["fA"])])])
    llm = FakeLLM(turns=turns, parsed=[memo])
    out = run_agent(cik=1, as_of=date(2023, 6, 1), llm=llm, con=con,
                    embedder=__import__("edgar.narrative.embedder",
                                        fromlist=["FakeEmbedder"]).FakeEmbedder(),
                    out_dir=tmp_path / "memos")
    assert out.sections[0].claims[0].citations == ["fA"]
    assert out.session_id and out.trace_id and out.config_version
```

- [ ] **Step 2: Run to verify failure** — ImportError expected.

- [ ] **Step 3: Implement** — three files. `tool_defs.py` first:

```python
import json
from datetime import date, datetime
from edgar.agent.ledger import LedgerEntry
from edgar.tools.facts_tools import get_facts, list_available_facts
from edgar.tools.compute import compute, ComputeError
from edgar.tools.peers import get_peer_set
from edgar.narrative.store import search_spans

TOOL_DEFS: list[dict] = [
    {"name": "get_facts",
     "description": "Canonical financial facts for one company and period "
                    "window. Missing fields come back with a status code — "
                    "cite fact_id values verbatim.",
     "input_schema": {"type": "object", "properties": {
         "cik": {"type": "integer"},
         "fields": {"type": "array", "items": {"type": "string"}},
         "period_start": {"type": "string", "description": "YYYY-MM-DD"},
         "period_end": {"type": "string", "description": "YYYY-MM-DD"}},
      "required": ["cik", "fields", "period_start", "period_end"]}},
    {"name": "search_filings",
     "description": "Search 10-K narrative (Items 1, 1A, 7). Returns spans "
                    "with span_id to cite.",
     "input_schema": {"type": "object", "properties": {
         "cik": {"type": "integer"}, "query": {"type": "string"},
         "items": {"type": "array", "items": {"type": "string"}}},
      "required": ["cik", "query"]}},
    {"name": "compute",
     "description": "The ONLY way to derive a number. Expression over "
                    "variables bound to fact_ids; returns value + "
                    "derivation_id to cite. + and - require like period "
                    "types; ratios may mix.",
     "input_schema": {"type": "object", "properties": {
         "expression": {"type": "string"},
         "inputs": {"type": "object",
                    "additionalProperties": {"type": "string"}}},
      "required": ["expression", "inputs"]}},
    {"name": "get_peer_set",
     "description": "Comparable companies by SIC, with the selection rule.",
     "input_schema": {"type": "object", "properties": {
         "cik": {"type": "integer"}, "min_peers": {"type": "integer"}},
      "required": ["cik"]}},
    {"name": "list_available_facts",
     "description": "Coverage map: which fields exist for which periods, "
                    "with status codes. Consult before claiming absence.",
     "input_schema": {"type": "object", "properties": {
         "cik": {"type": "integer"}},
      "required": ["cik"]}},
]


def _d(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def dispatch_tool(con, name, args, *, as_of, embedder, retrieval_k):
    try:
        if name == "get_facts":
            r = get_facts(con, int(args["cik"]), list(args["fields"]),
                          _d(args["period_start"]), _d(args["period_end"]),
                          as_of)
            entries = [LedgerEntry("fact", f.fact_id,
                                   f"{f.canonical_field} {f.period_end} "
                                   f"{f.unit} {f.value:g} "
                                   f"(filed {f.filed_date})", "")
                       for f in r.facts]
            return r.model_dump_json(), entries
        if name == "search_filings":
            hits = search_spans(con, args["query"], int(args["cik"]), as_of,
                                k=retrieval_k, embedder=embedder,
                                items=args.get("items"))
            entries = [LedgerEntry("span", h.span_id,
                                   f"{h.item} {h.accession}: "
                                   f"{h.text[:120]}", "",
                                   payload=h.text)
                       for h in hits]
            return json.dumps([h.model_dump(mode="json") for h in hits]), entries
        if name == "compute":
            c = compute(con, args["expression"], dict(args["inputs"]), as_of)
            entry = LedgerEntry("derivation", c.derivation_id,
                                f"{c.expression} = {c.value:g} "
                                f"inputs {c.inputs}", "")
            return c.model_dump_json(), [entry]
        if name == "get_peer_set":
            ps = get_peer_set(con, int(args["cik"]), as_of,
                              min_peers=int(args.get("min_peers", 10)))
            return ps.model_dump_json(), [LedgerEntry(
                "note", "", f"peer set ({len(ps.peers)}): "
                f"{ps.selection_rule}", "")]
        if name == "list_available_facts":
            rep = list_available_facts(con, int(args["cik"]), as_of)
            gist = "; ".join(
                f"{e.period_end}: " + ",".join(
                    f"{k}={v}" for k, v in sorted(e.statuses.items())
                    if v != "AVAILABLE")
                for e in rep.entries[:8]) or "all AVAILABLE"
            return rep.model_dump_json(), [LedgerEntry(
                "coverage", "", f"coverage: {gist}", "")]
        return json.dumps({"error": f"unknown tool {name}"}), []
    except (ComputeError, ValueError, KeyError, TypeError) as exc:
        return json.dumps({"error": str(exc)}), []
```

`nodes.py` — full implementation (the retrieve loop is the snippet above made concrete):

```python
import json
import uuid
from pathlib import Path
from edgar.agent.ledger import LedgerEntry
from edgar.agent.memo import Memo, render_markdown
from edgar.agent.guardrails import check_memo, repair_memo
from edgar.agent.tool_defs import TOOL_DEFS, dispatch_tool
from edgar.memory.procedural import SECTIONS, load_system_prompt, load_rubric
from edgar.memory.episodic import (
    recall_conclusions, save_session, record_conclusions)
from edgar.tools.facts_tools import list_available_facts

_CONCLUSION_SECTIONS = ("growth", "profitability", "cash_quality")


def load_memory(state: dict) -> dict:
    state["session_id"] = "S-" + uuid.uuid4().hex[:12]
    recalled = recall_conclusions(state["con"], state["cik"],
                                  state["as_of"],
                                  limit=state["config"].recall_limit)
    state["recalled_ids"] = [c.conclusion_id for c in recalled]
    for c in recalled:
        state["ledger"].append(LedgerEntry(
            "note", "", f"prior conclusion (learned {c.learned_as_of}): "
            f"{c.conclusion}", "memory"))
    return state


def coverage_node(state: dict) -> dict:
    rep = list_available_facts(state["con"], state["cik"], state["as_of"])
    state["coverage"] = rep
    gaps = sorted({f"{f}={st}" for e in rep.entries
                   for f, st in e.statuses.items() if st != "AVAILABLE"})
    state["ledger"].append(LedgerEntry(
        "coverage", "", "coverage gaps: " + ("; ".join(gaps) or "none"),
        "unanswered"))
    return state


def plan_node(state: dict) -> dict:
    state["plan"] = (["qa"] if state["question"]
                     else [slug for _, slug, _ in SECTIONS])
    return state


def _rubric_for(state: dict, slug: str) -> str:
    if slug == "qa":
        return ("Answer this question from tool evidence only, with "
                "citations; refuse with status codes when the store cannot "
                f"answer: {state['question']}")
    return load_rubric(slug)


def retrieve_section(state: dict) -> dict:
    cfg, slug = state["config"], state["plan"][state["section_idx"]]
    system = load_system_prompt() +         f"

Company CIK {state['cik']}, as_of {state['as_of']}."
    messages = [{"role": "user", "content": _rubric_for(state, slug)}]
    with state["tracer"].span(f"section:{slug}", cik=state["cik"]) as span:
        for _ in range(cfg.max_tool_turns):
            turn = state["llm"].tool_turn(system=system, messages=messages,
                                          tools=TOOL_DEFS)
            state["usage"]["in"] += turn.usage_in
            state["usage"]["out"] += turn.usage_out
            if not turn.tool_calls:
                if turn.text:
                    state["ledger"].append(
                        LedgerEntry("note", "", turn.text[:800], slug))
                break
            messages.append({"role": "assistant",
                             "content": turn.raw_content})
            results = []
            for call in turn.tool_calls:
                span.event("tool_call", tool=call.name)
                payload, entries = dispatch_tool(
                    state["con"], call.name, call.input,
                    as_of=state["as_of"], embedder=state["embedder"],
                    retrieval_k=cfg.retrieval_k)
                for e in entries:
                    e.section = slug
                    state["ledger"].append(e)
                results.append({"type": "tool_result",
                                "tool_use_id": call.id, "content": payload})
            messages.append({"role": "user", "content": results})
    state["section_idx"] += 1
    return state


def compact_node(state: dict) -> dict:
    cfg = state["config"]
    if state["ledger"].size_chars() > cfg.compaction_threshold_chars:
        freed = state["ledger"].compact()
        with state["tracer"].span("compaction") as span:
            span.event("compaction", freed=freed)
    return state


def write_memo(state: dict) -> dict:
    cfg = state["config"]
    section_list = "
".join(f"{n}. {slug}: {title}"
                             for n, slug, title in SECTIONS)
    prompt = (
        "Write the diligence memo as structured output.
"
        "Sections (use these slugs/titles, in order):
" + section_list +
        "

RULES: cite ONLY identifiers that appear in [brackets] in the "
        "evidence ledger below, copied verbatim. One assertion per claim. "
        "A section whose evidence is only status codes gets "
        "status='status_code' and a status_note. Value-creation ideas go "
        "in hypotheses, labeled.

EVIDENCE LEDGER:
" +
        state["ledger"].render()[:cfg.context_budget_chars])
    memo = state["llm"].parse_structured(
        system=load_system_prompt(), prompt=prompt, output_model=Memo)
    state["memo"] = memo.model_copy(update={
        "cik": state["cik"], "as_of": state["as_of"],
        "company_name": state["company_name"],
        "config_version": cfg.config_version,
        "trace_id": state["tracer"].trace_id,
        "session_id": state["session_id"]})
    return state


def guardrail_node(state: dict) -> dict:
    report = check_memo(state["con"], state["memo"], state["as_of"])
    state["guardrail_report"] = report
    with state["tracer"].span("guardrails") as span:
        span.event("guardrail", rejections=report.rejection_count)
    return state


def repair_node(state: dict) -> dict:
    state["memo"] = repair_memo(state["memo"], state["guardrail_report"])
    state["repair_round"] += 1
    return state


def emit(state: dict) -> dict:
    memo, con = state["memo"], state["con"]
    out_dir = Path(state.get("out_dir", "data/memos"))
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{memo.cik}_{memo.as_of}_{memo.config_version}"
    (out_dir / f"{stem}.md").write_text(render_markdown(memo))
    (out_dir / f"{stem}.json").write_text(json.dumps({
        "memo": memo.model_dump(mode="json"),
        "guardrail_rejections": state["guardrail_report"].rejection_count
        if state["guardrail_report"] else 0,
        "usage": state["usage"]}))
    save_session(con, session_id=memo.session_id, cik=memo.cik,
                 as_of=memo.as_of, config_version=memo.config_version,
                 trace_id=memo.trace_id, question=state["question"],
                 recalled_conclusion_ids=state["recalled_ids"])
    conclusions = [c.text for s in memo.sections
                   if s.slug in _CONCLUSION_SECTIONS
                   for c in s.claims if not c.is_hypothesis][:5]
    record_conclusions(con, session_id=memo.session_id, cik=memo.cik,
                       conclusions=conclusions, learned_as_of=memo.as_of,
                       trace_id=memo.trace_id)
    state["tracer"].flush()
    return state
```

Note `emit` reads `state.get("out_dir", …)` — tests set `state["out_dir"]`; `run_agent` passes its `out_dir` argument through the initial state. `guardrail_report` may be None only in the emit-without-guardrail test path; production flow always runs guardrail_node first.

`graph.py`:

```python
from pathlib import Path
from langgraph.graph import StateGraph, START, END
from edgar.agent import nodes

def build_graph():
    g = StateGraph(dict)
    for name in ("load_memory", "coverage_node", "plan_node",
                 "retrieve_section", "compact_node", "write_memo",
                 "guardrail_node", "repair_node", "emit"):
        g.add_node(name, getattr(nodes, name))
    g.add_edge(START, "load_memory")
    g.add_edge("load_memory", "coverage_node")
    g.add_edge("coverage_node", "plan_node")
    g.add_edge("plan_node", "retrieve_section")
    g.add_edge("retrieve_section", "compact_node")
    g.add_conditional_edges(
        "compact_node",
        lambda s: "retrieve_section" if s["section_idx"] < len(s["plan"])
        else "write_memo")
    g.add_edge("write_memo", "guardrail_node")
    g.add_conditional_edges(
        "guardrail_node",
        lambda s: "emit" if not s["guardrail_report"].rejection_count
        or s["repair_round"] >= s["config"].max_repair_rounds
        else "repair_node")
    g.add_edge("repair_node", "guardrail_node")
    g.add_edge("emit", END)
    return g.compile()
```

plus `run_agent(...)` assembling defaults (real `connect()`, `AnthropicLLM(cfg.generation_model)`, `SentenceTransformerEmbedder()` only when a search is possible — pass `FakeEmbedder` replacement via arg in tests), calling `build_graph().invoke(state)`, returning `state["memo"]`. `run.py`:

```python
"""CLI: venv/bin/python -m edgar.agent.run --cik 320193 --as-of 2024-03-01
        [--question "..."] [--config v1]"""
import argparse
from datetime import date
from edgar.config import load_secrets_env
from edgar.agent.graph import run_agent

def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--cik", type=int, required=True)
    p.add_argument("--as-of", required=True)
    p.add_argument("--question", default=None)
    p.add_argument("--config", default="v1")
    a = p.parse_args()
    load_secrets_env()
    memo = run_agent(cik=a.cik, as_of=date.fromisoformat(a.as_of),
                     question=a.question)
    print(f"memo written: data/memos/ (session {memo.session_id}, "
          f"trace {memo.trace_id})")

if __name__ == "__main__":
    main()
```

Makefile:

```make
memo:
	venv/bin/python -m edgar.agent.run --cik $(CIK) --as-of $(AS_OF)
```

**LangGraph sanity check before wiring:** run `venv/bin/python -c "from langgraph.graph import StateGraph, START, END; print('ok')"`. If that import fails on the pinned version, do NOT fight the framework — `graph.py` alternatively ships `build_graph()` returning a tiny sequential driver with the same `.invoke(state)` surface (a 20-line loop over the same node order and the same two conditionals). The nodes and tests do not change either way; note the choice in the commit.

- [ ] **Step 4: Run** — `venv/bin/pytest tests/test_agent_nodes.py tests/test_agent_graph.py -q` → PASS; full suite.

- [ ] **Step 5: First real memo (manual checkpoint, costs ~$1-3)** — requires `ANTHROPIC_API_KEY` in `.env`, the rebuilt store (Task 1), narrative index (Task 7):

```bash
make memo CIK=320193 AS_OF=2024-03-01
```

Open `data/memos/320193_2024-03-01_*.md`. Verify: sections present, citations bracketed, section 11 lists real status codes, no number appears without a citation. If Langfuse is up (Task 9), confirm the trace shows per-section spans with tool_call events. Fix obvious prompt problems in `prompts/` (that changes `config_version` — expected and correct).

- [ ] **Step 6: Commit**

```bash
git add src/edgar/agent tests/test_agent_nodes.py tests/test_agent_graph.py Makefile
git commit -m "feat(agent): section loop, LangGraph wiring, CLI; first real memo

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

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
