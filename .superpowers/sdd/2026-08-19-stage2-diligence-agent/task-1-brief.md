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

