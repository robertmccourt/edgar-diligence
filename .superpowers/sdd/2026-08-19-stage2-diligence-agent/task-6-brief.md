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

