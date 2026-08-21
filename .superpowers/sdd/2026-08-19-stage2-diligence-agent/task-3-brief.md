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

