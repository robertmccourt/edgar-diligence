# Stage 1: Bitemporal SEC Data Foundation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a point-in-time (bitemporal) SEC fundamentals store in DuckDB where every fact carries both the period it describes and the date it became publicly knowable, with a canonical schema, an auditable mapping layer, and a measurement of restatement and reporting-lag impact.

**Architecture:** SEC DERA quarterly ZIP archives are downloaded to an immutable raw zone and loaded into DuckDB with a `source_quarter` stamp. Because each archive is already a snapshot of what was on file at that moment, stacking them yields bitemporality directly. A deterministic mapping layer projects thousands of XBRL tags onto 10 canonical fields. An as-of query layer returns only what was knowable at a given date. Nothing is ever overwritten — restatements are appended and superseded by `filed_date`.

**Tech Stack:** Python 3.11+, DuckDB, httpx, Pydantic v2 + pydantic-settings, pytest, pandas (loading only).

**Spec:** `docs/superpowers/specs/2026-08-18-cited-diligence-agent-design.md`

## Global Constraints

- **SEC compliance:** every HTTP request to sec.gov MUST send a `User-Agent` header of the form `"<Name> <email>"`. Requests MUST be rate-limited to at most 10 per second. Bulk archives are preferred over per-company API calls.
- **Immutability:** the raw zone is append-only. Loaders never `UPDATE` or `DELETE` raw rows.
- **Bitemporality:** facts are never overwritten. A restatement is a new row with a later `filed_date`. Any query that does not filter on `filed_date <= as_of` is a bug.
- **Period type is enforced:** `duration` and `instant` facts are never mixed in a computation.
- **Determinism:** `fact_id` is a content hash, so rebuilding the database from the same archives produces identical IDs.
- **Canonical fields (exactly these 10 in v1):** `revenue`, `cost_of_revenue`, `gross_profit`, `operating_income`, `net_income`, `total_assets`, `total_liabilities`, `stockholders_equity`, `operating_cash_flow`, `capex`.
- **Time range:** 2019Q1 through the latest available quarter.
- **Spec amendment:** `MAPPING_RULE` gains a `priority INTEGER` column, required by §4.5 tier-2 structural resolution (lower number wins). Update the spec's ERD when this plan completes.

---

## File Structure

| File | Responsibility |
|---|---|
| `pyproject.toml` | Dependencies, pytest config |
| `src/edgar/config.py` | Settings: paths, SEC user agent, date range |
| `src/edgar/db.py` | DuckDB connection + schema DDL |
| `src/edgar/ingest/archives.py` | Quarter enumeration, URL construction, rate-limited download |
| `src/edgar/ingest/extract.py` | ZIP extraction + column schema validation |
| `src/edgar/ingest/load.py` | Load sub/num/tag/pre into raw tables |
| `src/edgar/curate/company.py` | Company dimension, fiscal year end, SIC to sector |
| `src/edgar/curate/periods.py` | `qtrs`/`ddate` to period_type/start/end |
| `src/edgar/curate/mapping.py` | Canonical schema, mapping rules, sign conventions |
| `src/edgar/curate/facts.py` | Fact table build, fact_id hashing |
| `src/edgar/query/asof.py` | As-of query layer |
| `src/edgar/query/coverage.py` | Missing-value taxonomy, coverage map |
| `src/edgar/curate/universe.py` | Eligibility screen |
| `src/edgar/analysis/restatement.py` | Restatement + filing-lag measurement |
| `src/edgar/quality/checks.py` | Data quality suite with thresholds |
| `docs/verification/dera-format.md` | Task 1 findings (written, not code) |

---

## Task 1: Verify DERA format and SEC access

**This task produces a written findings document, not code.** Every later task depends on its conclusions. Do not skip it, and do not proceed if findings contradict the assumptions below.

**Files:**
- Create: `docs/verification/dera-format.md`

**Interfaces:**
- Produces: confirmed archive URL template, confirmed column lists for `sub.txt` / `num.txt` / `tag.txt` / `pre.txt`, confirmed rate limit, answer on segment-dimension availability.

- [ ] **Step 1: Fetch current SEC documentation**

Read <https://www.sec.gov/dera/data/financial-statement-data-sets.html> and <https://www.sec.gov/os/webmaster-faq#developers>. Record the stated rate limit and User-Agent requirement verbatim.

- [ ] **Step 2: Download one archive by hand and inspect it**

```bash
mkdir -p /tmp/dera && cd /tmp/dera
curl -s -A "Robert McCourt rmmccourt01@comcast.net" \
  -o 2024q1.zip \
  "https://www.sec.gov/files/dera/data/financial-statement-data-sets/2024q1.zip"
unzip -o 2024q1.zip
for f in sub num tag pre; do echo "== $f =="; head -1 $f.txt | tr '\t' '\n' | nl; done
wc -l sub.txt num.txt tag.txt pre.txt
```

- [ ] **Step 3: Record findings**

Write `docs/verification/dera-format.md` answering exactly these questions:

1. What is the working archive URL template? (Expected: `https://www.sec.gov/files/dera/data/financial-statement-data-sets/{year}q{quarter}.zip`)
2. What is the earliest quarter still available, and the latest?
3. List the exact columns of each of the four files.
4. Does `num.txt` contain a `segments` column? If yes, what do non-empty values look like? **This determines whether segment-level analysis is possible in Stage 2.**
5. Confirm: `sub.txt.filed` is `YYYYMMDD`, `sub.txt.fye` is `MMDD`, `num.txt.ddate` is `YYYYMMDD`, `num.txt.qtrs` is an integer.
6. What is the stated rate limit?

- [ ] **Step 4: Flag contradictions**

If any assumption in the Global Constraints is contradicted — different URL, missing columns, different date formats — **stop and report before writing code**. Later tasks hard-code these.

- [ ] **Step 5: Commit**

```bash
git add docs/verification/dera-format.md
git commit -m "docs: verify DERA archive format and SEC access constraints"
```

---

## Task 2: Project scaffold, settings, and database connection

**Files:**
- Create: `pyproject.toml`, `src/edgar/__init__.py`, `src/edgar/config.py`, `src/edgar/db.py`
- Test: `tests/test_db.py`

**Interfaces:**
- Produces: `Settings` (fields `data_dir: Path`, `duckdb_path: Path`, `sec_user_agent: str`, `start_year: int`, `start_quarter: int`); `get_settings() -> Settings`; `connect(path: Path | None = None) -> duckdb.DuckDBPyConnection`; `init_schema(con) -> None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_db.py
from edgar.db import connect, init_schema

def test_init_schema_creates_raw_tables(tmp_path):
    con = connect(tmp_path / "t.duckdb")
    init_schema(con)
    names = {r[0] for r in con.execute(
        "SELECT table_name FROM information_schema.tables"
    ).fetchall()}
    assert {"raw_sub", "raw_num", "raw_tag", "raw_pre"} <= names

def test_init_schema_is_idempotent(tmp_path):
    con = connect(tmp_path / "t.duckdb")
    init_schema(con)
    init_schema(con)
    assert con.execute("SELECT count(*) FROM raw_sub").fetchone()[0] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_db.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'edgar'`

- [ ] **Step 3: Write pyproject.toml**

```toml
[project]
name = "edgar-diligence"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "duckdb>=1.0",
    "httpx>=0.27",
    "pydantic>=2.7",
    "pydantic-settings>=2.3",
    "pandas>=2.2",
]

[project.optional-dependencies]
dev = ["pytest>=8.0", "pytest-cov>=5.0"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/edgar"]

[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]
```

- [ ] **Step 4: Write config.py**

```python
# src/edgar/config.py
from functools import lru_cache
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EDGAR_", env_file=".env")

    data_dir: Path = Path("data")
    duckdb_path: Path = Path("data/edgar.duckdb")
    sec_user_agent: str = "Robert McCourt rmmccourt01@comcast.net"
    start_year: int = 2019
    start_quarter: int = 1

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 5: Write db.py**

```python
# src/edgar/db.py
from pathlib import Path
import duckdb
from edgar.config import get_settings

RAW_DDL = """
CREATE TABLE IF NOT EXISTS raw_sub (
    adsh VARCHAR, cik BIGINT, name VARCHAR, sic VARCHAR, fye VARCHAR,
    form VARCHAR, period VARCHAR, fy VARCHAR, fp VARCHAR, filed VARCHAR,
    prevrpt VARCHAR, detail VARCHAR, nciks VARCHAR, source_quarter VARCHAR
);
CREATE TABLE IF NOT EXISTS raw_num (
    adsh VARCHAR, tag VARCHAR, version VARCHAR, coreg VARCHAR,
    ddate VARCHAR, qtrs VARCHAR, uom VARCHAR, value VARCHAR,
    footnote VARCHAR, segments VARCHAR, source_quarter VARCHAR
);
CREATE TABLE IF NOT EXISTS raw_tag (
    tag VARCHAR, version VARCHAR, custom VARCHAR, abstract VARCHAR,
    datatype VARCHAR, iord VARCHAR, crdr VARCHAR, tlabel VARCHAR,
    doc VARCHAR, source_quarter VARCHAR
);
CREATE TABLE IF NOT EXISTS raw_pre (
    adsh VARCHAR, report VARCHAR, line VARCHAR, stmt VARCHAR,
    inpth VARCHAR, tag VARCHAR, version VARCHAR, plabel VARCHAR,
    negating VARCHAR, source_quarter VARCHAR
);
"""


def connect(path: Path | None = None) -> duckdb.DuckDBPyConnection:
    target = path or get_settings().duckdb_path
    target.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(target))


def init_schema(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(RAW_DDL)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pip install -e ".[dev]" && pytest tests/test_db.py -v`
Expected: 2 passed

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml src/edgar tests/test_db.py
git commit -m "feat: project scaffold with DuckDB raw zone schema"
```

---

## Task 3: Quarter enumeration and archive URLs

**Files:**
- Create: `src/edgar/ingest/__init__.py`, `src/edgar/ingest/archives.py`
- Test: `tests/test_archives.py`

**Interfaces:**
- Consumes: `get_settings()` from Task 2.
- Produces: `Quarter` (frozen dataclass, fields `year: int`, `quarter: int`, property `label -> str` e.g. `"2024q1"`); `enumerate_quarters(start: Quarter, end: Quarter) -> list[Quarter]`; `archive_url(q: Quarter) -> str`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_archives.py
import pytest
from edgar.ingest.archives import Quarter, enumerate_quarters, archive_url

def test_quarter_label():
    assert Quarter(2024, 1).label == "2024q1"

def test_enumerate_spans_year_boundary():
    qs = enumerate_quarters(Quarter(2023, 3), Quarter(2024, 2))
    assert [q.label for q in qs] == ["2023q3", "2023q4", "2024q1", "2024q2"]

def test_enumerate_single_quarter():
    assert [q.label for q in enumerate_quarters(Quarter(2024, 1), Quarter(2024, 1))] == ["2024q1"]

def test_enumerate_rejects_reversed_range():
    with pytest.raises(ValueError):
        enumerate_quarters(Quarter(2024, 2), Quarter(2024, 1))

def test_archive_url():
    assert archive_url(Quarter(2024, 1)) == (
        "https://www.sec.gov/files/dera/data/"
        "financial-statement-data-sets/2024q1.zip"
    )

def test_quarter_rejects_out_of_range():
    with pytest.raises(ValueError):
        Quarter(2024, 5)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_archives.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'edgar.ingest'`

- [ ] **Step 3: Write archives.py**

```python
# src/edgar/ingest/archives.py
from dataclasses import dataclass

BASE_URL = "https://www.sec.gov/files/dera/data/financial-statement-data-sets"


@dataclass(frozen=True, order=True)
class Quarter:
    year: int
    quarter: int

    def __post_init__(self) -> None:
        if not 1 <= self.quarter <= 4:
            raise ValueError(f"quarter must be 1-4, got {self.quarter}")

    @property
    def label(self) -> str:
        return f"{self.year}q{self.quarter}"

    def next(self) -> "Quarter":
        if self.quarter == 4:
            return Quarter(self.year + 1, 1)
        return Quarter(self.year, self.quarter + 1)


def enumerate_quarters(start: Quarter, end: Quarter) -> list[Quarter]:
    if start > end:
        raise ValueError(f"start {start.label} is after end {end.label}")
    out, cur = [], start
    while cur <= end:
        out.append(cur)
        cur = cur.next()
    return out


def archive_url(q: Quarter) -> str:
    return f"{BASE_URL}/{q.label}.zip"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_archives.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/edgar/ingest tests/test_archives.py
git commit -m "feat: DERA quarter enumeration and archive URL construction"
```

---

## Task 4: Rate-limited downloader

**Files:**
- Modify: `src/edgar/ingest/archives.py`
- Test: `tests/test_download.py`

**Interfaces:**
- Consumes: `Quarter`, `archive_url` from Task 3.
- Produces: `RateLimiter(max_per_second: float)` with method `acquire() -> None`; `download_archive(q: Quarter, dest_dir: Path, client=None, limiter=None) -> Path`. Returns the existing path without re-downloading if the file is already present.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_download.py
import httpx, pytest
from edgar.ingest.archives import Quarter, download_archive, RateLimiter

def _client(payload=b"zipbytes"):
    def handler(request):
        assert request.headers["user-agent"], "SEC requires a User-Agent"
        return httpx.Response(200, content=payload)
    return httpx.Client(transport=httpx.MockTransport(handler))

def test_download_writes_file(tmp_path):
    p = download_archive(Quarter(2024, 1), tmp_path, client=_client())
    assert p.name == "2024q1.zip"
    assert p.read_bytes() == b"zipbytes"

def test_download_skips_existing(tmp_path):
    (tmp_path / "2024q1.zip").write_bytes(b"cached")
    p = download_archive(Quarter(2024, 1), tmp_path, client=_client(b"fresh"))
    assert p.read_bytes() == b"cached"

def test_download_raises_on_404(tmp_path):
    c = httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(404)))
    with pytest.raises(httpx.HTTPStatusError):
        download_archive(Quarter(1990, 1), tmp_path, client=c)

def test_rate_limiter_spaces_calls():
    import time
    lim = RateLimiter(max_per_second=20)
    t0 = time.monotonic()
    for _ in range(3):
        lim.acquire()
    assert time.monotonic() - t0 >= 0.09
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_download.py -v`
Expected: FAIL with `ImportError: cannot import name 'download_archive'`

- [ ] **Step 3: Append to archives.py**

```python
# append to src/edgar/ingest/archives.py
import time
from pathlib import Path
import httpx
from edgar.config import get_settings


class RateLimiter:
    """SEC permits at most 10 requests/second. Default leaves headroom."""

    def __init__(self, max_per_second: float = 8.0) -> None:
        self._interval = 1.0 / max_per_second
        self._last = 0.0

    def acquire(self) -> None:
        wait = self._interval - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()


_DEFAULT_LIMITER = RateLimiter()


def download_archive(
    q: Quarter,
    dest_dir: Path,
    client: httpx.Client | None = None,
    limiter: RateLimiter | None = None,
) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{q.label}.zip"
    if dest.exists():
        return dest

    (limiter or _DEFAULT_LIMITER).acquire()
    owns = client is None
    client = client or httpx.Client(
        headers={"User-Agent": get_settings().sec_user_agent},
        timeout=120.0,
        follow_redirects=True,
    )
    try:
        resp = client.get(archive_url(q))
        resp.raise_for_status()
        dest.write_bytes(resp.content)
    finally:
        if owns:
            client.close()
    return dest
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_download.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/edgar/ingest/archives.py tests/test_download.py
git commit -m "feat: SEC-compliant rate-limited archive downloader"
```

---

## Task 5: Archive extraction with column validation

**Files:**
- Create: `src/edgar/ingest/extract.py`
- Test: `tests/test_extract.py`

**Interfaces:**
- Produces: `REQUIRED_COLUMNS: dict[str, set[str]]`; `SchemaMismatch(Exception)`; `extract_archive(zip_path: Path, dest_dir: Path) -> dict[str, Path]` returning `{"sub": ..., "num": ..., "tag": ..., "pre": ...}`; `validate_columns(path: Path, kind: str) -> None`.

This is the guard against Task 1's assumptions silently drifting.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_extract.py
import zipfile, pytest
from edgar.ingest.extract import extract_archive, validate_columns, SchemaMismatch

SUB_HEADER = "adsh\tcik\tname\tsic\tfye\tform\tperiod\tfy\tfp\tfiled\tprevrpt\tdetail\tnciks"

def _zip(tmp_path, files):
    z = tmp_path / "a.zip"
    with zipfile.ZipFile(z, "w") as zf:
        for name, body in files.items():
            zf.writestr(name, body)
    return z

def test_extract_returns_four_paths(tmp_path):
    z = _zip(tmp_path, {
        "sub.txt": SUB_HEADER + "\n",
        "num.txt": "adsh\ttag\tversion\tcoreg\tddate\tqtrs\tuom\tvalue\tfootnote\n",
        "tag.txt": "tag\tversion\tcustom\tabstract\tdatatype\tiord\tcrdr\ttlabel\tdoc\n",
        "pre.txt": "adsh\treport\tline\tstmt\tinpth\ttag\tversion\tplabel\tnegating\n",
    })
    out = extract_archive(z, tmp_path / "x")
    assert set(out) == {"sub", "num", "tag", "pre"}
    assert out["sub"].exists()

def test_validate_columns_accepts_superset(tmp_path):
    p = tmp_path / "num.txt"
    p.write_text("adsh\ttag\tversion\tcoreg\tddate\tqtrs\tuom\tvalue\tfootnote\tsegments\n")
    validate_columns(p, "num")  # extra 'segments' column is fine

def test_validate_columns_rejects_missing(tmp_path):
    p = tmp_path / "num.txt"
    p.write_text("adsh\ttag\tvalue\n")
    with pytest.raises(SchemaMismatch) as e:
        validate_columns(p, "num")
    assert "ddate" in str(e.value)

def test_extract_rejects_incomplete_archive(tmp_path):
    z = _zip(tmp_path, {"sub.txt": SUB_HEADER + "\n"})
    with pytest.raises(SchemaMismatch):
        extract_archive(z, tmp_path / "y")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_extract.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'edgar.ingest.extract'`

- [ ] **Step 3: Write extract.py**

```python
# src/edgar/ingest/extract.py
import zipfile
from pathlib import Path

REQUIRED_COLUMNS: dict[str, set[str]] = {
    "sub": {"adsh", "cik", "name", "sic", "fye", "form",
            "period", "fy", "fp", "filed"},
    "num": {"adsh", "tag", "version", "ddate", "qtrs", "uom", "value"},
    "tag": {"tag", "version", "custom", "datatype", "iord", "crdr"},
    "pre": {"adsh", "stmt", "tag", "version", "plabel"},
}


class SchemaMismatch(Exception):
    """A DERA file does not have the columns this codebase assumes."""


def validate_columns(path: Path, kind: str) -> None:
    with path.open(encoding="utf-8", errors="replace") as fh:
        header = fh.readline().rstrip("\n\r").split("\t")
    missing = REQUIRED_COLUMNS[kind] - set(header)
    if missing:
        raise SchemaMismatch(
            f"{path.name} missing required columns {sorted(missing)}; "
            f"found {header}. Re-run Task 1 verification."
        )


def extract_archive(zip_path: Path, dest_dir: Path) -> dict[str, Path]:
    dest_dir.mkdir(parents=True, exist_ok=True)
    out: dict[str, Path] = {}
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
        for kind in REQUIRED_COLUMNS:
            member = f"{kind}.txt"
            if member not in names:
                raise SchemaMismatch(
                    f"{zip_path.name} has no {member}; found {sorted(names)}"
                )
            zf.extract(member, dest_dir)
            path = dest_dir / member
            validate_columns(path, kind)
            out[kind] = path
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_extract.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/edgar/ingest/extract.py tests/test_extract.py
git commit -m "feat: archive extraction with fail-loud column validation"
```

---

## Task 6: Raw zone loader

**Files:**
- Create: `src/edgar/ingest/load.py`
- Test: `tests/test_load.py`

**Interfaces:**
- Consumes: `connect`, `init_schema` (Task 2); `extract_archive` (Task 5); `Quarter` (Task 3).
- Produces: `load_quarter(con, files: dict[str, Path], q: Quarter) -> dict[str, int]` returning row counts per table. Idempotent: re-loading the same quarter replaces that quarter's rows rather than duplicating them.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_load.py
from edgar.db import connect, init_schema
from edgar.ingest.archives import Quarter
from edgar.ingest.load import load_quarter

def _files(tmp_path):
    (tmp_path / "sub.txt").write_text(
        "adsh\tcik\tname\tsic\tfye\tform\tperiod\tfy\tfp\tfiled\tprevrpt\tdetail\tnciks\n"
        "0000320193-24-000081\t320193\tAPPLE INC\t3571\t0930\t10-Q\t20240630\t2024\tQ3\t20240802\t0\t1\t1\n")
    (tmp_path / "num.txt").write_text(
        "adsh\ttag\tversion\tcoreg\tddate\tqtrs\tuom\tvalue\tfootnote\n"
        "0000320193-24-000081\tRevenues\tus-gaap/2024\t\t20240630\t1\tUSD\t85777000000\t\n")
    (tmp_path / "tag.txt").write_text(
        "tag\tversion\tcustom\tabstract\tdatatype\tiord\tcrdr\ttlabel\tdoc\n"
        "Revenues\tus-gaap/2024\t0\t0\tmonetary\tD\tC\tRevenues\tTotal revenue.\n")
    (tmp_path / "pre.txt").write_text(
        "adsh\treport\tline\tstmt\tinpth\ttag\tversion\tplabel\tnegating\n"
        "0000320193-24-000081\t2\t1\tIS\t0\tRevenues\tus-gaap/2024\tNet sales\t0\n")
    return {k: tmp_path / f"{k}.txt" for k in ("sub", "num", "tag", "pre")}

def test_load_populates_all_tables(tmp_path):
    con = connect(tmp_path / "t.duckdb"); init_schema(con)
    counts = load_quarter(con, _files(tmp_path), Quarter(2024, 3))
    assert counts == {"sub": 1, "num": 1, "tag": 1, "pre": 1}
    assert con.execute("SELECT source_quarter FROM raw_num").fetchone()[0] == "2024q3"

def test_load_is_idempotent(tmp_path):
    con = connect(tmp_path / "t.duckdb"); init_schema(con)
    f = _files(tmp_path)
    load_quarter(con, f, Quarter(2024, 3))
    load_quarter(con, f, Quarter(2024, 3))
    assert con.execute("SELECT count(*) FROM raw_num").fetchone()[0] == 1

def test_load_handles_missing_optional_column(tmp_path):
    """num.txt has no 'segments' column in older quarters; load must NULL-fill."""
    con = connect(tmp_path / "t.duckdb"); init_schema(con)
    load_quarter(con, _files(tmp_path), Quarter(2024, 3))
    assert con.execute("SELECT segments FROM raw_num").fetchone()[0] is None

def test_load_keeps_quarters_separate(tmp_path):
    con = connect(tmp_path / "t.duckdb"); init_schema(con)
    f = _files(tmp_path)
    load_quarter(con, f, Quarter(2024, 3))
    load_quarter(con, f, Quarter(2024, 4))
    assert con.execute("SELECT count(*) FROM raw_num").fetchone()[0] == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_load.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'edgar.ingest.load'`

- [ ] **Step 3: Write load.py**

```python
# src/edgar/ingest/load.py
from pathlib import Path
import duckdb
from edgar.ingest.archives import Quarter

TABLES = {"sub": "raw_sub", "num": "raw_num", "tag": "raw_tag", "pre": "raw_pre"}


def load_quarter(
    con: duckdb.DuckDBPyConnection,
    files: dict[str, Path],
    q: Quarter,
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for kind, table in TABLES.items():
        con.execute(f"DELETE FROM {table} WHERE source_quarter = ?", [q.label])

        table_cols = [r[0] for r in con.execute(
            f"SELECT column_name FROM information_schema.columns "
            f"WHERE table_name = '{table}' AND column_name <> 'source_quarter'"
        ).fetchall()]

        # DERA adds and removes optional columns between quarters (e.g. the
        # 'segments' column in num.txt). Intersect the file's actual header
        # with the table's columns and NULL-fill the rest, so neither an
        # extra nor a missing optional column breaks the load.
        with files[kind].open(encoding="utf-8", errors="replace") as fh:
            file_cols = set(fh.readline().rstrip("\n\r").split("\t"))

        select = ", ".join(
            f'"{c}"' if c in file_cols else f'NULL AS "{c}"'
            for c in table_cols
        )
        con.execute(
            f"""
            INSERT INTO {table}
            SELECT {select}, '{q.label}'
            FROM read_csv(?, delim='\t', header=true, all_varchar=true,
                          quote='', escape='', ignore_errors=true)
            """,
            [str(files[kind])],
        )
        counts[kind] = con.execute(
            f"SELECT count(*) FROM {table} WHERE source_quarter = ?", [q.label]
        ).fetchone()[0]
    return counts
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_load.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/edgar/ingest/load.py tests/test_load.py
git commit -m "feat: idempotent raw zone loader stamped by source quarter"
```

---

## Task 7: Period model — qtrs and ddate to typed periods

**Files:**
- Create: `src/edgar/curate/__init__.py`, `src/edgar/curate/periods.py`
- Test: `tests/test_periods.py`

**Interfaces:**
- Produces: `PeriodType` (`str` enum, values `"instant"`, `"duration"`); `Period` (frozen dataclass: `period_type: PeriodType`, `start: date | None`, `end: date`); `parse_period(ddate: str, qtrs: str) -> Period`; `parse_yyyymmdd(s: str) -> date`.

DERA encodes `qtrs=0` as a point-in-time balance, `qtrs=1` as one quarter, `qtrs=4` as a full year. Mixing these is the modeling bug §4.4 forbids.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_periods.py
from datetime import date
import pytest
from edgar.curate.periods import parse_period, PeriodType, parse_yyyymmdd

def test_qtrs_zero_is_instant():
    p = parse_period("20240630", "0")
    assert p.period_type == PeriodType.INSTANT
    assert p.start is None
    assert p.end == date(2024, 6, 30)

def test_qtrs_one_is_three_month_duration():
    p = parse_period("20240630", "1")
    assert p.period_type == PeriodType.DURATION
    assert p.start == date(2024, 4, 1)
    assert p.end == date(2024, 6, 30)

def test_qtrs_four_is_annual_duration():
    p = parse_period("20240930", "4")
    assert p.start == date(2023, 10, 1)
    assert p.end == date(2024, 9, 30)

def test_qtrs_two_is_six_month_duration():
    p = parse_period("20240630", "2")
    assert p.start == date(2024, 1, 1)

def test_parse_yyyymmdd():
    assert parse_yyyymmdd("20240630") == date(2024, 6, 30)

def test_rejects_bad_date():
    with pytest.raises(ValueError):
        parse_period("2024063", "0")

def test_rejects_negative_qtrs():
    with pytest.raises(ValueError):
        parse_period("20240630", "-1")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_periods.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'edgar.curate'`

- [ ] **Step 3: Write periods.py**

```python
# src/edgar/curate/periods.py
from dataclasses import dataclass
from datetime import date
from enum import StrEnum


class PeriodType(StrEnum):
    INSTANT = "instant"
    DURATION = "duration"


@dataclass(frozen=True)
class Period:
    period_type: PeriodType
    start: date | None
    end: date


def parse_yyyymmdd(s: str) -> date:
    s = s.strip()
    if len(s) != 8 or not s.isdigit():
        raise ValueError(f"expected YYYYMMDD, got {s!r}")
    return date(int(s[:4]), int(s[4:6]), int(s[6:]))


def _minus_months(d: date, months: int) -> date:
    total = d.year * 12 + (d.month - 1) - months
    return date(total // 12, total % 12 + 1, 1)


def parse_period(ddate: str, qtrs: str) -> Period:
    end = parse_yyyymmdd(ddate)
    try:
        n = int(str(qtrs).strip())
    except ValueError as exc:
        raise ValueError(f"qtrs not an integer: {qtrs!r}") from exc
    if n < 0:
        raise ValueError(f"qtrs must be non-negative, got {n}")
    if n == 0:
        return Period(PeriodType.INSTANT, None, end)
    return Period(PeriodType.DURATION, _minus_months(end, n * 3), end)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_periods.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add src/edgar/curate tests/test_periods.py
git commit -m "feat: typed period model separating instant and duration facts"
```

---

## Task 8: Canonical schema and deterministic mapping rules

**Files:**
- Create: `src/edgar/curate/mapping.py`
- Test: `tests/test_mapping.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `CANONICAL_FIELDS: tuple[str, ...]`; `MappingRule` (frozen dataclass: `mapping_rule_id`, `source_tag`, `taxonomy`, `canonical_field`, `sign_convention: int`, `scale: float`, `method: str`, `confidence: float`, `priority: int`, `rationale: str`); `SEED_RULES: tuple[MappingRule, ...]`; `rules_for_tag(tag: str) -> list[MappingRule]`; `create_mapping_table(con) -> None`; `seed_mapping_rules(con) -> int`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_mapping.py
from edgar.db import connect
from edgar.curate.mapping import (
    CANONICAL_FIELDS, SEED_RULES, rules_for_tag,
    create_mapping_table, seed_mapping_rules,
)

def test_exactly_ten_canonical_fields():
    assert len(CANONICAL_FIELDS) == 10
    assert "revenue" in CANONICAL_FIELDS
    assert "capex" in CANONICAL_FIELDS

def test_every_rule_targets_a_canonical_field():
    for r in SEED_RULES:
        assert r.canonical_field in CANONICAL_FIELDS

def test_mapping_rule_ids_are_unique():
    ids = [r.mapping_rule_id for r in SEED_RULES]
    assert len(ids) == len(set(ids))

def test_revenue_has_multiple_candidate_tags():
    tags = {r.source_tag for r in SEED_RULES if r.canonical_field == "revenue"}
    assert "Revenues" in tags
    assert "RevenueFromContractWithCustomerExcludingAssessedTax" in tags

def test_priority_disambiguates_revenue_tags():
    rs = sorted(
        (r for r in SEED_RULES if r.canonical_field == "revenue"),
        key=lambda r: r.priority,
    )
    assert rs[0].source_tag == "RevenueFromContractWithCustomerExcludingAssessedTax"

def test_rules_for_tag_returns_matches():
    assert rules_for_tag("Assets")[0].canonical_field == "total_assets"
    assert rules_for_tag("NoSuchTag") == []

def test_seed_writes_rules_to_db(tmp_path):
    con = connect(tmp_path / "t.duckdb")
    create_mapping_table(con)
    n = seed_mapping_rules(con)
    assert n == len(SEED_RULES)
    seed_mapping_rules(con)  # idempotent
    assert con.execute("SELECT count(*) FROM mapping_rule").fetchone()[0] == n
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_mapping.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'edgar.curate.mapping'`

- [ ] **Step 3: Write mapping.py**

```python
# src/edgar/curate/mapping.py
from dataclasses import dataclass, astuple
import duckdb

CANONICAL_FIELDS: tuple[str, ...] = (
    "revenue", "cost_of_revenue", "gross_profit", "operating_income",
    "net_income", "total_assets", "total_liabilities",
    "stockholders_equity", "operating_cash_flow", "capex",
)


@dataclass(frozen=True)
class MappingRule:
    mapping_rule_id: str
    source_tag: str
    taxonomy: str
    canonical_field: str
    sign_convention: int
    scale: float
    method: str
    confidence: float
    priority: int
    rationale: str


def _r(n: int, tag: str, field: str, priority: int, rationale: str,
       sign: int = 1) -> MappingRule:
    return MappingRule(
        mapping_rule_id=f"MR-{n:04d}", source_tag=tag, taxonomy="us-gaap",
        canonical_field=field, sign_convention=sign, scale=1.0,
        method="deterministic", confidence=1.0, priority=priority,
        rationale=rationale,
    )


SEED_RULES: tuple[MappingRule, ...] = (
    _r(1, "RevenueFromContractWithCustomerExcludingAssessedTax", "revenue", 1,
       "ASC 606 primary revenue element; preferred post-2018."),
    _r(2, "RevenueFromContractWithCustomerIncludingAssessedTax", "revenue", 2,
       "ASC 606 variant including assessed tax."),
    _r(3, "Revenues", "revenue", 3, "Generic total revenue element."),
    _r(4, "SalesRevenueNet", "revenue", 4, "Pre-ASC-606 element; legacy filings."),
    _r(5, "CostOfRevenue", "cost_of_revenue", 1, "Total cost of revenue."),
    _r(6, "CostOfGoodsAndServicesSold", "cost_of_revenue", 2,
       "Combined goods and services cost."),
    _r(7, "CostOfGoodsSold", "cost_of_revenue", 3, "Goods-only cost; legacy."),
    _r(8, "GrossProfit", "gross_profit", 1, "Reported gross profit."),
    _r(9, "OperatingIncomeLoss", "operating_income", 1,
       "Standard operating income element."),
    _r(10, "NetIncomeLoss", "net_income", 1,
        "Net income attributable to the parent."),
    _r(11, "ProfitLoss", "net_income", 2,
        "Includes noncontrolling interests; used when NetIncomeLoss absent."),
    _r(12, "Assets", "total_assets", 1, "Total assets."),
    _r(13, "Liabilities", "total_liabilities", 1, "Total liabilities."),
    _r(14, "StockholdersEquity", "stockholders_equity", 1,
        "Parent-only stockholders equity."),
    _r(15, "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
        "stockholders_equity", 2, "Total equity including NCI."),
    _r(16, "NetCashProvidedByUsedInOperatingActivities",
        "operating_cash_flow", 1, "Operating cash flow."),
    _r(17, "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
        "operating_cash_flow", 2, "Continuing-operations variant."),
    _r(18, "PaymentsToAcquirePropertyPlantAndEquipment", "capex", 1,
        "Capex reported as a positive payment (outflow); normalized positive."),
    _r(19, "PaymentsToAcquireProductiveAssets", "capex", 2,
        "Broader productive-asset purchases."),
)

_BY_TAG: dict[str, list[MappingRule]] = {}
for _rule in SEED_RULES:
    _BY_TAG.setdefault(_rule.source_tag, []).append(_rule)


def rules_for_tag(tag: str) -> list[MappingRule]:
    return sorted(_BY_TAG.get(tag, []), key=lambda r: r.priority)


MAPPING_DDL = """
CREATE TABLE IF NOT EXISTS mapping_rule (
    mapping_rule_id VARCHAR PRIMARY KEY,
    source_tag VARCHAR,
    taxonomy VARCHAR,
    canonical_field VARCHAR,
    sign_convention INTEGER,
    scale DOUBLE,
    method VARCHAR,
    confidence DOUBLE,
    priority INTEGER,
    rationale VARCHAR
);
"""


def create_mapping_table(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(MAPPING_DDL)


def seed_mapping_rules(con: duckdb.DuckDBPyConnection) -> int:
    con.execute("DELETE FROM mapping_rule WHERE method = 'deterministic'")
    con.executemany(
        "INSERT INTO mapping_rule VALUES (?,?,?,?,?,?,?,?,?,?)",
        [list(astuple(r)) for r in SEED_RULES],
    )
    return len(SEED_RULES)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_mapping.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add src/edgar/curate/mapping.py tests/test_mapping.py
git commit -m "feat: canonical schema and seeded deterministic mapping rules"
```

---

## Task 9: Company dimension with fiscal calendar

**Files:**
- Create: `src/edgar/curate/company.py`
- Test: `tests/test_company.py`

**Interfaces:**
- Consumes: `raw_sub` (Task 6).
- Produces: `sic_to_sector(sic: str | None) -> str`; `fye_to_month(fye: str | None) -> int | None`; `build_company_table(con) -> int`. Creates table `company` with columns `cik, name, sic, sector, fiscal_year_end_month, first_filing_date, eligibility_status, exclusion_reason`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_company.py
from edgar.db import connect, init_schema
from edgar.curate.company import sic_to_sector, fye_to_month, build_company_table

def test_sic_to_sector_financials():
    assert sic_to_sector("6021") == "financials"
    assert sic_to_sector("6798") == "financials"

def test_sic_to_sector_manufacturing_and_utilities():
    assert sic_to_sector("3571") == "manufacturing"
    assert sic_to_sector("4911") == "utilities"

def test_sic_to_sector_handles_missing():
    assert sic_to_sector(None) == "unknown"
    assert sic_to_sector("") == "unknown"

def test_fye_to_month():
    assert fye_to_month("0930") == 9
    assert fye_to_month("1231") == 12
    assert fye_to_month("") is None
    assert fye_to_month("bad") is None

def test_build_company_table(tmp_path):
    con = connect(tmp_path / "t.duckdb"); init_schema(con)
    con.execute("""INSERT INTO raw_sub VALUES
        ('a1','320193','APPLE INC','3571','0930','10-K','20230930','2023','FY','20231103','0','1','1','2023q4'),
        ('a2','320193','APPLE INC','3571','0930','10-Q','20240630','2024','Q3','20240802','0','1','1','2024q3'),
        ('b1','19617','JPMORGAN','6021','1231','10-K','20231231','2023','FY','20240216','0','1','1','2024q1')""")
    n = build_company_table(con)
    assert n == 2
    row = con.execute(
        "SELECT sector, fiscal_year_end_month, first_filing_date "
        "FROM company WHERE cik = 320193").fetchone()
    assert row[0] == "manufacturing"
    assert row[1] == 9
    assert str(row[2]) == "2023-11-03"
    assert con.execute(
        "SELECT sector FROM company WHERE cik = 19617").fetchone()[0] == "financials"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_company.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'edgar.curate.company'`

- [ ] **Step 3: Write company.py**

```python
# src/edgar/curate/company.py
import duckdb

_SECTOR_RANGES: tuple[tuple[int, int, str], ...] = (
    (100, 999, "agriculture"),
    (1000, 1499, "mining"),
    (1500, 1799, "construction"),
    (2000, 3999, "manufacturing"),
    (4000, 4899, "transport_communications"),
    (4900, 4999, "utilities"),
    (5000, 5199, "wholesale"),
    (5200, 5999, "retail"),
    (6000, 6799, "financials"),
    (7000, 8999, "services"),
    (9100, 9999, "public_administration"),
)


def sic_to_sector(sic: str | None) -> str:
    if not sic or not str(sic).strip().isdigit():
        return "unknown"
    code = int(str(sic).strip())
    for lo, hi, name in _SECTOR_RANGES:
        if lo <= code <= hi:
            return name
    return "unknown"


def fye_to_month(fye: str | None) -> int | None:
    if not fye:
        return None
    s = str(fye).strip()
    if len(s) != 4 or not s.isdigit():
        return None
    month = int(s[:2])
    return month if 1 <= month <= 12 else None


COMPANY_DDL = """
CREATE OR REPLACE TABLE company AS
SELECT
    CAST(cik AS BIGINT)                       AS cik,
    any_value(name)                           AS name,
    any_value(sic)                            AS sic,
    NULL::VARCHAR                             AS sector,
    NULL::INTEGER                             AS fiscal_year_end_month,
    min(strptime(filed, '%Y%m%d')::DATE)      AS first_filing_date,
    'pending'::VARCHAR                        AS eligibility_status,
    NULL::VARCHAR                             AS exclusion_reason
FROM raw_sub
WHERE cik IS NOT NULL AND trim(cik) <> ''
GROUP BY CAST(cik AS BIGINT);
"""


def build_company_table(con: duckdb.DuckDBPyConnection) -> int:
    con.execute(COMPANY_DDL)
    rows = con.execute("SELECT cik, sic FROM company").fetchall()
    fye = {
        int(c): f for c, f in con.execute(
            "SELECT CAST(cik AS BIGINT), any_value(fye) FROM raw_sub "
            "WHERE cik IS NOT NULL AND trim(cik) <> '' "
            "GROUP BY CAST(cik AS BIGINT)"
        ).fetchall()
    }
    con.executemany(
        "UPDATE company SET sector = ?, fiscal_year_end_month = ? WHERE cik = ?",
        [(sic_to_sector(sic), fye_to_month(fye.get(cik)), cik)
         for cik, sic in rows],
    )
    return len(rows)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_company.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/edgar/curate/company.py tests/test_company.py
git commit -m "feat: company dimension with sector and fiscal calendar"
```

---

## Task 10: Fact table build with deterministic fact_id

**Files:**
- Create: `src/edgar/curate/facts.py`
- Test: `tests/test_facts.py`

**Interfaces:**
- Consumes: `raw_num`, `raw_sub` (Task 6); `mapping_rule` (Task 8); `parse_period` (Task 7).
- Produces: `make_fact_id(adsh, tag, ddate, qtrs, uom, coreg) -> str` (16-char sha256 prefix); `create_fact_table(con) -> None`; `build_facts(con) -> int`. Creates table `fact` per spec §4.3, with `filed_date` joined from `raw_sub`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_facts.py
from edgar.db import connect, init_schema
from edgar.curate.mapping import create_mapping_table, seed_mapping_rules
from edgar.curate.facts import make_fact_id, create_fact_table, build_facts

def _seed(con):
    init_schema(con); create_mapping_table(con); seed_mapping_rules(con)
    con.execute("""INSERT INTO raw_sub VALUES
        ('a1','320193','APPLE','3571','0930','10-Q','20240630','2024','Q3','20240802','0','1','1','2024q3')""")
    con.execute("""INSERT INTO raw_num VALUES
        ('a1','Revenues','us-gaap/2024','','20240630','1','USD','85777000000','','','2024q3'),
        ('a1','Assets','us-gaap/2024','','20240630','0','USD','331612000000','','','2024q3'),
        ('a1','SomeCustomTag','apple/2024','','20240630','1','USD','123','','','2024q3')""")

def test_fact_id_is_deterministic():
    a = make_fact_id("a1", "Revenues", "20240630", "1", "USD", "")
    b = make_fact_id("a1", "Revenues", "20240630", "1", "USD", "")
    assert a == b and len(a) == 16

def test_fact_id_differs_by_period():
    assert make_fact_id("a1", "Revenues", "20240630", "1", "USD", "") != \
           make_fact_id("a1", "Revenues", "20240331", "1", "USD", "")

def test_build_facts_maps_known_tags_only(tmp_path):
    con = connect(tmp_path / "t.duckdb"); _seed(con)
    create_fact_table(con)
    n = build_facts(con)
    assert n == 2  # custom tag is not mapped
    fields = {r[0] for r in con.execute(
        "SELECT canonical_field FROM fact").fetchall()}
    assert fields == {"revenue", "total_assets"}

def test_build_facts_sets_period_type_and_filed_date(tmp_path):
    con = connect(tmp_path / "t.duckdb"); _seed(con)
    create_fact_table(con); build_facts(con)
    row = con.execute(
        "SELECT period_type, period_start, period_end, filed_date "
        "FROM fact WHERE canonical_field = 'revenue'").fetchone()
    assert row[0] == "duration"
    assert str(row[1]) == "2024-04-01"
    assert str(row[2]) == "2024-06-30"
    assert str(row[3]) == "2024-08-02"

def test_instant_fact_has_null_start(tmp_path):
    con = connect(tmp_path / "t.duckdb"); _seed(con)
    create_fact_table(con); build_facts(con)
    row = con.execute(
        "SELECT period_type, period_start FROM fact "
        "WHERE canonical_field = 'total_assets'").fetchone()
    assert row[0] == "instant" and row[1] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_facts.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'edgar.curate.facts'`

- [ ] **Step 3: Write facts.py**

```python
# src/edgar/curate/facts.py
import hashlib
import duckdb
from edgar.curate.periods import parse_period

FACT_DDL = """
CREATE TABLE IF NOT EXISTS fact (
    fact_id VARCHAR PRIMARY KEY,
    cik BIGINT,
    canonical_field VARCHAR,
    value DOUBLE,
    unit VARCHAR,
    period_type VARCHAR,
    period_start DATE,
    period_end DATE,
    fiscal_year VARCHAR,
    fiscal_period VARCHAR,
    filed_date DATE,
    accession VARCHAR,
    source_tag VARCHAR,
    mapping_rule_id VARCHAR,
    confidence DOUBLE,
    source_quarter VARCHAR
);
"""


def make_fact_id(adsh: str, tag: str, ddate: str, qtrs: str,
                 uom: str, coreg: str) -> str:
    key = "|".join([adsh, tag, ddate, str(qtrs), uom, coreg or ""])
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def create_fact_table(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(FACT_DDL)


def build_facts(con: duckdb.DuckDBPyConnection) -> int:
    """Project mapped raw_num rows into the bitemporal fact table.

    Only the highest-priority rule for each tag is applied. Rows whose tag
    has no rule are skipped — they are the mapping tail, surfaced by the
    coverage report rather than silently dropped.
    """
    rows = con.execute(
        """
        WITH best AS (
            SELECT source_tag, canonical_field, sign_convention, scale,
                   mapping_rule_id, confidence,
                   row_number() OVER (PARTITION BY source_tag
                                      ORDER BY priority) AS rn
            FROM mapping_rule
        )
        SELECT n.adsh, n.tag, n.ddate, n.qtrs, n.uom, coalesce(n.coreg, ''),
               n.value, s.cik, s.filed, s.fy, s.fp, n.source_quarter,
               b.canonical_field, b.sign_convention, b.scale,
               b.mapping_rule_id, b.confidence
        FROM raw_num n
        JOIN raw_sub s
          ON s.adsh = n.adsh AND s.source_quarter = n.source_quarter
        JOIN best b ON b.source_tag = n.tag AND b.rn = 1
        WHERE n.value IS NOT NULL AND trim(n.value) <> ''
          AND coalesce(n.coreg, '') = ''
        """
    ).fetchall()

    payload = []
    for (adsh, tag, ddate, qtrs, uom, coreg, value, cik, filed, fy, fp,
         src_q, field, sign, scale, rule_id, conf) in rows:
        try:
            period = parse_period(ddate, qtrs)
            numeric = float(value) * sign * scale
            filed_date = f"{filed[:4]}-{filed[4:6]}-{filed[6:]}"
        except (ValueError, TypeError):
            continue
        payload.append((
            make_fact_id(adsh, tag, ddate, qtrs, uom, coreg),
            int(cik), field, numeric, uom, str(period.period_type),
            period.start, period.end, fy, fp, filed_date, adsh, tag,
            rule_id, conf, src_q,
        ))

    con.executemany(
        "INSERT OR REPLACE INTO fact VALUES "
        "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        payload,
    )
    return len(payload)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_facts.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/edgar/curate/facts.py tests/test_facts.py
git commit -m "feat: bitemporal fact table with deterministic content-hash IDs"
```

---

## Task 11: As-of query layer

**This is the highest-value task in the plan.** Its tests are the proof that the point-in-time property actually holds.

**Files:**
- Create: `src/edgar/query/__init__.py`, `src/edgar/query/asof.py`
- Test: `tests/test_asof.py`

**Interfaces:**
- Consumes: `fact` table (Task 10).
- Produces: `AsOfFact` (frozen dataclass: `fact_id`, `cik`, `canonical_field`, `value`, `unit`, `period_type`, `period_start`, `period_end`, `filed_date`, `accession`, `source_tag`, `mapping_rule_id`); `get_facts_asof(con, cik, fields, period_start, period_end, as_of) -> list[AsOfFact]`; `restatement_history(con, cik, field, period_end) -> list[AsOfFact]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_asof.py
from datetime import date
from edgar.db import connect
from edgar.curate.facts import create_fact_table
from edgar.query.asof import get_facts_asof, restatement_history

def _restated(tmp_path):
    """Revenue for Q1 2023 first reported as 100, later restated to 94."""
    con = connect(tmp_path / "t.duckdb")
    create_fact_table(con)
    con.executemany(
        "INSERT INTO fact VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            ("f1", 1, "revenue", 100.0, "USD", "duration",
             date(2023, 1, 1), date(2023, 3, 31), "2023", "Q1",
             date(2023, 5, 10), "acc-1", "Revenues", "MR-0003", 1.0, "2023q2"),
            ("f2", 1, "revenue", 94.0, "USD", "duration",
             date(2023, 1, 1), date(2023, 3, 31), "2023", "Q1",
             date(2023, 11, 8), "acc-2", "Revenues", "MR-0003", 1.0, "2023q4"),
        ],
    )
    return con

def test_before_first_filing_returns_nothing(tmp_path):
    con = _restated(tmp_path)
    assert get_facts_asof(con, 1, ["revenue"],
                          date(2023, 1, 1), date(2023, 12, 31),
                          date(2023, 4, 30)) == []

def test_between_filings_returns_original_value(tmp_path):
    con = _restated(tmp_path)
    got = get_facts_asof(con, 1, ["revenue"], date(2023, 1, 1),
                         date(2023, 12, 31), date(2023, 9, 1))
    assert len(got) == 1 and got[0].value == 100.0
    assert got[0].accession == "acc-1"

def test_after_restatement_returns_corrected_value(tmp_path):
    con = _restated(tmp_path)
    got = get_facts_asof(con, 1, ["revenue"], date(2023, 1, 1),
                         date(2023, 12, 31), date(2024, 1, 1))
    assert len(got) == 1 and got[0].value == 94.0
    assert got[0].accession == "acc-2"

def test_as_of_equals_filed_date_is_inclusive(tmp_path):
    con = _restated(tmp_path)
    got = get_facts_asof(con, 1, ["revenue"], date(2023, 1, 1),
                         date(2023, 12, 31), date(2023, 5, 10))
    assert got[0].value == 100.0

def test_restatement_history_returns_both_versions(tmp_path):
    con = _restated(tmp_path)
    hist = restatement_history(con, 1, "revenue", date(2023, 3, 31))
    assert [h.value for h in hist] == [100.0, 94.0]

def test_unknown_field_returns_empty(tmp_path):
    con = _restated(tmp_path)
    assert get_facts_asof(con, 1, ["capex"], date(2023, 1, 1),
                          date(2023, 12, 31), date(2024, 1, 1)) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_asof.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'edgar.query'`

- [ ] **Step 3: Write asof.py**

```python
# src/edgar/query/asof.py
from dataclasses import dataclass
from datetime import date
import duckdb

_COLUMNS = """
    fact_id, cik, canonical_field, value, unit, period_type,
    period_start, period_end, filed_date, accession, source_tag,
    mapping_rule_id
"""


@dataclass(frozen=True)
class AsOfFact:
    fact_id: str
    cik: int
    canonical_field: str
    value: float
    unit: str
    period_type: str
    period_start: date | None
    period_end: date
    filed_date: date
    accession: str
    source_tag: str
    mapping_rule_id: str


def get_facts_asof(
    con: duckdb.DuckDBPyConnection,
    cik: int,
    fields: list[str],
    period_start: date,
    period_end: date,
    as_of: date,
) -> list[AsOfFact]:
    """Return the value that was knowable on `as_of`.

    For each (field, period) the row with the greatest filed_date not later
    than as_of wins. Rows filed after as_of are invisible — this is the
    point-in-time guarantee.
    """
    if not fields:
        return []
    placeholders = ", ".join("?" for _ in fields)
    rows = con.execute(
        f"""
        SELECT {_COLUMNS} FROM (
            SELECT {_COLUMNS},
                   row_number() OVER (
                       PARTITION BY canonical_field, period_end, period_type
                       ORDER BY filed_date DESC, accession DESC
                   ) AS rn
            FROM fact
            WHERE cik = ?
              AND canonical_field IN ({placeholders})
              AND period_end BETWEEN ? AND ?
              AND filed_date <= ?
        ) WHERE rn = 1
        ORDER BY canonical_field, period_end
        """,
        [cik, *fields, period_start, period_end, as_of],
    ).fetchall()
    return [AsOfFact(*r) for r in rows]


def restatement_history(
    con: duckdb.DuckDBPyConnection,
    cik: int,
    field: str,
    period_end: date,
) -> list[AsOfFact]:
    """Every reported version of one figure, oldest filing first."""
    rows = con.execute(
        f"""
        SELECT {_COLUMNS} FROM fact
        WHERE cik = ? AND canonical_field = ? AND period_end = ?
        ORDER BY filed_date ASC
        """,
        [cik, field, period_end],
    ).fetchall()
    return [AsOfFact(*r) for r in rows]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_asof.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/edgar/query tests/test_asof.py
git commit -m "feat: as-of query layer enforcing point-in-time visibility"
```

---

## Task 12: Coverage map and missing-value taxonomy

**Files:**
- Create: `src/edgar/query/coverage.py`
- Test: `tests/test_coverage.py`

**Interfaces:**
- Consumes: `fact` (Task 10), `raw_num` (Task 6), `mapping_rule` (Task 8), `CANONICAL_FIELDS` (Task 8).
- Produces: `FieldStatus` (StrEnum: `AVAILABLE`, `NOT_DISCLOSED`, `NOT_YET_FILED`, `UNMAPPED`); `coverage_map(con, cik, period_end, as_of) -> dict[str, FieldStatus]`.

Implements spec §4.6. The `NOT_DISCLOSED` vs `UNMAPPED` distinction is the point: one is a claim about the company, the other a claim about us.

**Deliberate deferral:** spec §4.6 also defines `NOT_APPLICABLE` (the field is meaningless for this business type). That classification depends on the memo's business-type rules, which are Stage 2 procedural memory, not Stage 1 data. It is intentionally absent from `FieldStatus` here and must be added when Stage 2 introduces those rules. The other four statuses plus `AVAILABLE` are all implemented.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_coverage.py
from datetime import date
from edgar.db import connect, init_schema
from edgar.curate.mapping import create_mapping_table, seed_mapping_rules
from edgar.curate.facts import create_fact_table
from edgar.query.coverage import coverage_map, FieldStatus

def _db(tmp_path):
    con = connect(tmp_path / "t.duckdb")
    init_schema(con); create_mapping_table(con); seed_mapping_rules(con)
    create_fact_table(con)
    con.execute("""INSERT INTO raw_sub VALUES
        ('a1','1','CO','3571','1231','10-K','20231231','2023','FY','20240215','0','1','1','2024q1')""")
    return con

def test_mapped_and_filed_is_available(tmp_path):
    con = _db(tmp_path)
    con.execute("""INSERT INTO fact VALUES
        ('f1',1,'revenue',100.0,'USD','duration','2023-01-01','2023-12-31',
         '2023','FY','2024-02-15','a1','Revenues','MR-0003',1.0,'2024q1')""")
    con.execute("""INSERT INTO raw_num VALUES
        ('a1','Revenues','us-gaap/2023','','20231231','4','USD','100','','','2024q1')""")
    m = coverage_map(con, 1, date(2023, 12, 31), date(2024, 6, 1))
    assert m["revenue"] == FieldStatus.AVAILABLE

def test_filed_after_as_of_is_not_yet_filed(tmp_path):
    con = _db(tmp_path)
    con.execute("""INSERT INTO fact VALUES
        ('f1',1,'revenue',100.0,'USD','duration','2023-01-01','2023-12-31',
         '2023','FY','2024-02-15','a1','Revenues','MR-0003',1.0,'2024q1')""")
    m = coverage_map(con, 1, date(2023, 12, 31), date(2024, 1, 10))
    assert m["revenue"] == FieldStatus.NOT_YET_FILED

def test_unmapped_when_related_tag_present_but_no_rule(tmp_path):
    con = _db(tmp_path)
    con.execute("""INSERT INTO raw_num VALUES
        ('a1','AcmeCostOfProductRevenue','acme/2023','','20231231','4','USD','60','','','2024q1')""")
    m = coverage_map(con, 1, date(2023, 12, 31), date(2024, 6, 1))
    assert m["cost_of_revenue"] == FieldStatus.UNMAPPED

def test_not_disclosed_when_nothing_resembles_the_concept(tmp_path):
    con = _db(tmp_path)
    con.execute("""INSERT INTO raw_num VALUES
        ('a1','Assets','us-gaap/2023','','20231231','0','USD','500','','','2024q1')""")
    m = coverage_map(con, 1, date(2023, 12, 31), date(2024, 6, 1))
    assert m["cost_of_revenue"] == FieldStatus.NOT_DISCLOSED

def test_conflicting_tags_for_same_field_are_ambiguous(tmp_path):
    con = _db(tmp_path)
    con.executemany(
        "INSERT INTO fact VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [("f1",1,"revenue",100.0,"USD","duration",date(2023,1,1),
          date(2023,12,31),"2023","FY",date(2024,2,15),"a1","Revenues",
          "MR-0003",1.0,"2024q1"),
         ("f2",1,"revenue",118.0,"USD","duration",date(2023,1,1),
          date(2023,12,31),"2023","FY",date(2024,2,15),"a1",
          "RevenueFromContractWithCustomerIncludingAssessedTax",
          "MR-0002",1.0,"2024q1")])
    m = coverage_map(con, 1, date(2023, 12, 31), date(2024, 6, 1))
    assert m["revenue"] == FieldStatus.AMBIGUOUS

def test_every_canonical_field_gets_a_status(tmp_path):
    con = _db(tmp_path)
    m = coverage_map(con, 1, date(2023, 12, 31), date(2024, 6, 1))
    assert len(m) == 10
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_coverage.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'edgar.query.coverage'`

- [ ] **Step 3: Write coverage.py**

```python
# src/edgar/query/coverage.py
from datetime import date
from enum import StrEnum
import duckdb
from edgar.curate.mapping import CANONICAL_FIELDS


class FieldStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    NOT_DISCLOSED = "NOT_DISCLOSED"
    NOT_YET_FILED = "NOT_YET_FILED"
    UNMAPPED = "UNMAPPED"
    AMBIGUOUS = "AMBIGUOUS"


# Substrings that suggest a tag is about a canonical concept even when no
# mapping rule matches it. Used only to separate "we missed it" (UNMAPPED)
# from "the company never reported it" (NOT_DISCLOSED).
_CONCEPT_HINTS: dict[str, tuple[str, ...]] = {
    "revenue": ("revenue", "sales"),
    "cost_of_revenue": ("costofrevenue", "costofgoods", "costofsales",
                        "costofproduct", "costofservice"),
    "gross_profit": ("grossprofit", "grossmargin"),
    "operating_income": ("operatingincome", "operatingprofit", "operatingloss"),
    "net_income": ("netincome", "netloss", "profitloss"),
    "total_assets": ("assets",),
    "total_liabilities": ("liabilities",),
    "stockholders_equity": ("equity",),
    "operating_cash_flow": ("operatingactivities",),
    "capex": ("propertyplantandequipment", "capitalexpenditure",
              "productiveassets"),
}


def coverage_map(
    con: duckdb.DuckDBPyConnection,
    cik: int,
    period_end: date,
    as_of: date,
) -> dict[str, FieldStatus]:
    """Classify every canonical field for one company-period per spec §4.6."""
    available = {
        r[0] for r in con.execute(
            "SELECT DISTINCT canonical_field FROM fact "
            "WHERE cik = ? AND period_end = ? AND filed_date <= ?",
            [cik, period_end, as_of],
        ).fetchall()
    }
    # Two mapped tags claiming the same canonical field with different values
    # in the same filing cannot be silently resolved (spec §4.6 AMBIGUOUS).
    ambiguous = {
        r[0] for r in con.execute(
            """
            SELECT canonical_field FROM fact
            WHERE cik = ? AND period_end = ? AND filed_date <= ?
            GROUP BY canonical_field, period_type, filed_date
            HAVING count(DISTINCT value) > 1
            """,
            [cik, period_end, as_of],
        ).fetchall()
    }
    filed_later = {
        r[0] for r in con.execute(
            "SELECT DISTINCT canonical_field FROM fact "
            "WHERE cik = ? AND period_end = ? AND filed_date > ?",
            [cik, period_end, as_of],
        ).fetchall()
    }
    mapped_tags = {
        r[0] for r in con.execute(
            "SELECT DISTINCT source_tag FROM mapping_rule").fetchall()
    }
    company_tags = [
        r[0] for r in con.execute(
            """
            SELECT DISTINCT n.tag FROM raw_num n
            JOIN raw_sub s ON s.adsh = n.adsh
                          AND s.source_quarter = n.source_quarter
            WHERE CAST(s.cik AS BIGINT) = ?
              AND strptime(s.filed, '%Y%m%d')::DATE <= ?
            """,
            [cik, as_of],
        ).fetchall()
    ]
    unmapped_lower = [t.lower() for t in company_tags if t not in mapped_tags]

    out: dict[str, FieldStatus] = {}
    for field in CANONICAL_FIELDS:
        if field in ambiguous:
            out[field] = FieldStatus.AMBIGUOUS
        elif field in available:
            out[field] = FieldStatus.AVAILABLE
        elif field in filed_later:
            out[field] = FieldStatus.NOT_YET_FILED
        elif any(h in t for t in unmapped_lower
                 for h in _CONCEPT_HINTS[field]):
            out[field] = FieldStatus.UNMAPPED
        else:
            out[field] = FieldStatus.NOT_DISCLOSED
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_coverage.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/edgar/query/coverage.py tests/test_coverage.py
git commit -m "feat: coverage map distinguishing NOT_DISCLOSED from UNMAPPED"
```

---

## Task 13: Universe eligibility screen

**Files:**
- Create: `src/edgar/curate/universe.py`
- Test: `tests/test_universe.py`

**Interfaces:**
- Consumes: `company` (Task 9), `fact` (Task 10).
- Produces: `EXCLUDED_SECTORS: frozenset[str]`; `MIN_QUARTERS: int = 12`; `MIN_FIELDS: int = 5`; `apply_eligibility(con) -> dict[str, int]` returning removal counts keyed by rule name, and updating `company.eligibility_status` / `company.exclusion_reason`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_universe.py
from edgar.db import connect
from edgar.curate.facts import create_fact_table
from edgar.curate.universe import apply_eligibility

def _db(tmp_path):
    con = connect(tmp_path / "t.duckdb")
    create_fact_table(con)
    con.execute("""CREATE TABLE company (
        cik BIGINT, name VARCHAR, sic VARCHAR, sector VARCHAR,
        fiscal_year_end_month INTEGER, first_filing_date DATE,
        eligibility_status VARCHAR, exclusion_reason VARCHAR)""")
    con.execute("""INSERT INTO company VALUES
        (1,'CLEAN','3571','manufacturing',12,'2019-01-01','pending',NULL),
        (2,'BANK','6021','financials',12,'2019-01-01','pending',NULL),
        (3,'POWER','4911','utilities',12,'2019-01-01','pending',NULL),
        (4,'YOUNG','3571','manufacturing',12,'2024-01-01','pending',NULL),
        (5,'SPARSE','3571','manufacturing',12,'2019-01-01','pending',NULL)""")
    fields = ["revenue","net_income","total_assets","total_liabilities",
              "stockholders_equity","operating_cash_flow"]
    rows = []
    for q in range(14):  # 14 distinct period_ends
        pe = f"20{19 + q // 4}-{3 * (q % 4) + 1:02d}-28"
        for f in fields:
            rows.append((f"c1-{q}-{f}", 1, f, 1.0, "USD", "duration",
                         None, pe, "2020", "Q1", pe, "a", "T", "MR-0001",
                         1.0, "x"))
        rows.append((f"c5-{q}", 5, "revenue", 1.0, "USD", "duration",
                     None, pe, "2020", "Q1", pe, "a", "T", "MR-0001", 1.0, "x"))
        rows.append((f"c4-{q}", 4, "revenue", 1.0, "USD", "duration",
                     None, pe, "2020", "Q1", pe, "a", "T", "MR-0001", 1.0, "x"))
    con.executemany(
        "INSERT INTO fact VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    return con

def test_clean_company_is_eligible(tmp_path):
    con = _db(tmp_path); apply_eligibility(con)
    assert con.execute(
        "SELECT eligibility_status FROM company WHERE cik=1").fetchone()[0] == "eligible"

def test_financials_and_utilities_excluded(tmp_path):
    con = _db(tmp_path); apply_eligibility(con)
    for cik in (2, 3):
        status, reason = con.execute(
            "SELECT eligibility_status, exclusion_reason FROM company WHERE cik=?",
            [cik]).fetchone()
        assert status == "excluded" and reason == "excluded_sector"

def test_insufficient_field_coverage_excluded(tmp_path):
    con = _db(tmp_path); apply_eligibility(con)
    status, reason = con.execute(
        "SELECT eligibility_status, exclusion_reason FROM company WHERE cik=5"
    ).fetchone()
    assert status == "excluded" and reason == "insufficient_field_coverage"

def test_returns_removal_counts(tmp_path):
    con = _db(tmp_path)
    counts = apply_eligibility(con)
    assert counts["excluded_sector"] == 2
    assert counts["eligible"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_universe.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'edgar.curate.universe'`

- [ ] **Step 3: Write universe.py**

```python
# src/edgar/curate/universe.py
import duckdb

EXCLUDED_SECTORS: frozenset[str] = frozenset({"financials", "utilities"})
MIN_QUARTERS: int = 12
MIN_FIELDS: int = 5


def apply_eligibility(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    """Classify every company per spec §4.7 and report removal counts.

    Rules are applied in order; the first failure wins, so exclusion_reason
    is always the most fundamental disqualifier.
    """
    con.execute(
        "UPDATE company SET eligibility_status='pending', exclusion_reason=NULL")

    con.execute(
        f"""
        UPDATE company SET eligibility_status='excluded',
                           exclusion_reason='excluded_sector'
        WHERE sector IN ({", ".join("?" * len(EXCLUDED_SECTORS))})
        """,
        sorted(EXCLUDED_SECTORS),
    )

    con.execute(
        """
        UPDATE company SET eligibility_status='excluded',
                           exclusion_reason='insufficient_history'
        WHERE eligibility_status='pending' AND cik IN (
            SELECT cik FROM fact GROUP BY cik
            HAVING count(DISTINCT period_end) < ?
        )
        """,
        [MIN_QUARTERS],
    )

    con.execute(
        """
        UPDATE company SET eligibility_status='excluded',
                           exclusion_reason='no_facts'
        WHERE eligibility_status='pending'
          AND cik NOT IN (SELECT DISTINCT cik FROM fact)
        """
    )

    con.execute(
        """
        UPDATE company SET eligibility_status='excluded',
                           exclusion_reason='insufficient_field_coverage'
        WHERE eligibility_status='pending' AND cik IN (
            SELECT cik FROM fact GROUP BY cik
            HAVING count(DISTINCT canonical_field) < ?
        )
        """,
        [MIN_FIELDS],
    )

    con.execute(
        "UPDATE company SET eligibility_status='eligible' "
        "WHERE eligibility_status='pending'")

    counts = {
        r[0]: r[1] for r in con.execute(
            "SELECT coalesce(exclusion_reason, eligibility_status), count(*) "
            "FROM company GROUP BY 1"
        ).fetchall()
    }
    return counts
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_universe.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/edgar/curate/universe.py tests/test_universe.py
git commit -m "feat: documented universe eligibility screen with removal counts"
```

---

## Task 14: Restatement and reporting-lag measurement

**Files:**
- Create: `src/edgar/analysis/__init__.py`, `src/edgar/analysis/restatement.py`
- Test: `tests/test_restatement.py`

**Interfaces:**
- Consumes: `fact` (Task 10).
- Produces: `restatement_stats(con) -> dict` with keys `total_figures`, `restated_figures`, `restatement_rate`, `median_abs_pct_change`, `max_abs_pct_change`; `filing_lag_stats(con) -> dict` with keys `n`, `median_days`, `p90_days`; `restatement_detail(con, min_abs_pct=0.0) -> list[tuple]`.

This produces the headline empirical result of Stage 1.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_restatement.py
from datetime import date
from edgar.db import connect
from edgar.curate.facts import create_fact_table
from edgar.analysis.restatement import (
    restatement_stats, filing_lag_stats, restatement_detail)

def _f(fid, cik, field, val, pe, filed):
    return (fid, cik, field, val, "USD", "duration",
            None, pe, "2023", "Q1", filed, f"acc-{fid}", "Revenues",
            "MR-0003", 1.0, "q")

def _db(tmp_path, rows):
    con = connect(tmp_path / "t.duckdb"); create_fact_table(con)
    con.executemany(
        "INSERT INTO fact VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    return con

def test_no_restatement_when_single_filing(tmp_path):
    con = _db(tmp_path, [_f("a", 1, "revenue", 100.0,
                            date(2023, 3, 31), date(2023, 5, 10))])
    s = restatement_stats(con)
    assert s["total_figures"] == 1
    assert s["restated_figures"] == 0
    assert s["restatement_rate"] == 0.0

def test_detects_restatement_and_magnitude(tmp_path):
    con = _db(tmp_path, [
        _f("a", 1, "revenue", 100.0, date(2023, 3, 31), date(2023, 5, 10)),
        _f("b", 1, "revenue", 94.0, date(2023, 3, 31), date(2023, 11, 8)),
    ])
    s = restatement_stats(con)
    assert s["restated_figures"] == 1
    assert s["restatement_rate"] == 1.0
    assert abs(s["median_abs_pct_change"] - 6.0) < 1e-9

def test_identical_revalue_is_not_a_restatement(tmp_path):
    con = _db(tmp_path, [
        _f("a", 1, "revenue", 100.0, date(2023, 3, 31), date(2023, 5, 10)),
        _f("b", 1, "revenue", 100.0, date(2023, 3, 31), date(2023, 11, 8)),
    ])
    assert restatement_stats(con)["restated_figures"] == 0

def test_filing_lag(tmp_path):
    con = _db(tmp_path, [
        _f("a", 1, "revenue", 100.0, date(2023, 3, 31), date(2023, 5, 10)),
        _f("b", 2, "revenue", 100.0, date(2023, 3, 31), date(2023, 5, 20)),
    ])
    lag = filing_lag_stats(con)
    assert lag["n"] == 2
    assert 40 <= lag["median_days"] <= 50

def test_detail_filters_by_magnitude(tmp_path):
    con = _db(tmp_path, [
        _f("a", 1, "revenue", 100.0, date(2023, 3, 31), date(2023, 5, 10)),
        _f("b", 1, "revenue", 94.0, date(2023, 3, 31), date(2023, 11, 8)),
    ])
    assert len(restatement_detail(con, min_abs_pct=1.0)) == 1
    assert len(restatement_detail(con, min_abs_pct=10.0)) == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_restatement.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'edgar.analysis'`

- [ ] **Step 3: Write restatement.py**

```python
# src/edgar/analysis/restatement.py
import duckdb

# A "figure" is one (cik, canonical_field, period_end, period_type) key.
# It is restated when two filings report materially different values for it.
_VERSIONS = """
WITH ordered AS (
    SELECT cik, canonical_field, period_end, period_type, value, filed_date,
           first_value(value) OVER (
               PARTITION BY cik, canonical_field, period_end, period_type
               ORDER BY filed_date
           ) AS first_value,
           count(*) OVER (
               PARTITION BY cik, canonical_field, period_end, period_type
           ) AS n_versions
    FROM fact
),
figures AS (
    SELECT cik, canonical_field, period_end, period_type,
           any_value(first_value) AS original,
           max(n_versions) AS n_versions,
           max(CASE WHEN first_value <> 0
                    THEN abs(value - first_value) / abs(first_value) * 100
                    ELSE 0 END) AS abs_pct_change
    FROM ordered
    GROUP BY cik, canonical_field, period_end, period_type
)
"""


def restatement_stats(con: duckdb.DuckDBPyConnection) -> dict:
    row = con.execute(
        _VERSIONS + """
        SELECT count(*),
               count(*) FILTER (WHERE abs_pct_change > 0.0001),
               median(abs_pct_change) FILTER (WHERE abs_pct_change > 0.0001),
               max(abs_pct_change)
        FROM figures
        """
    ).fetchone()
    total, restated, med, mx = row
    return {
        "total_figures": total,
        "restated_figures": restated,
        "restatement_rate": (restated / total) if total else 0.0,
        "median_abs_pct_change": med or 0.0,
        "max_abs_pct_change": mx or 0.0,
    }


def restatement_detail(
    con: duckdb.DuckDBPyConnection, min_abs_pct: float = 0.0
) -> list[tuple]:
    return con.execute(
        _VERSIONS + """
        SELECT cik, canonical_field, period_end, original,
               n_versions, abs_pct_change
        FROM figures
        WHERE abs_pct_change >= ? AND abs_pct_change > 0.0001
        ORDER BY abs_pct_change DESC
        """,
        [min_abs_pct],
    ).fetchall()


def filing_lag_stats(con: duckdb.DuckDBPyConnection) -> dict:
    row = con.execute(
        """
        WITH first_filing AS (
            SELECT cik, canonical_field, period_end,
                   min(filed_date) AS first_filed
            FROM fact
            GROUP BY cik, canonical_field, period_end
        )
        SELECT count(*),
               median(date_diff('day', period_end, first_filed)),
               quantile_cont(date_diff('day', period_end, first_filed), 0.9)
        FROM first_filing
        """
    ).fetchone()
    return {"n": row[0], "median_days": row[1], "p90_days": row[2]}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_restatement.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/edgar/analysis tests/test_restatement.py
git commit -m "feat: restatement frequency and reporting-lag measurement"
```

---

## Task 15: Data quality suite and pipeline entry point

**Files:**
- Create: `src/edgar/quality/__init__.py`, `src/edgar/quality/checks.py`, `src/edgar/pipeline.py`, `Makefile`
- Test: `tests/test_quality.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `QualityResult` (frozen dataclass: `name: str`, `passed: bool`, `observed: float`, `threshold: float`, `detail: str`); `run_quality_checks(con) -> list[QualityResult]`; `build_all(con, start: Quarter, end: Quarter, raw_dir: Path) -> dict`.

Thresholds are documented values, not magic numbers. Each has a stated rationale.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_quality.py
from datetime import date
from edgar.db import connect
from edgar.curate.facts import create_fact_table
from edgar.quality.checks import run_quality_checks

def _f(fid, cik, field, val, ptype, pstart, pe, filed):
    return (fid, cik, field, val, "USD", ptype, pstart, pe,
            "2023", "Q1", filed, "acc", "Revenues", "MR-0003", 1.0, "q")

def _db(tmp_path, rows):
    con = connect(tmp_path / "t.duckdb"); create_fact_table(con)
    con.executemany(
        "INSERT INTO fact VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    return con

def test_clean_data_passes_all_checks(tmp_path):
    con = _db(tmp_path, [
        _f("a", 1, "revenue", 100.0, "duration",
           date(2023, 1, 1), date(2023, 3, 31), date(2023, 5, 10)),
        _f("b", 1, "total_assets", 500.0, "instant",
           None, date(2023, 3, 31), date(2023, 5, 10)),
    ])
    results = run_quality_checks(con)
    failed = [r.name for r in results if not r.passed]
    assert failed == []

def test_detects_filed_before_period_end(tmp_path):
    con = _db(tmp_path, [
        _f("a", 1, "revenue", 100.0, "duration",
           date(2023, 1, 1), date(2023, 3, 31), date(2023, 1, 15)),
    ])
    r = next(x for x in run_quality_checks(con) if x.name == "filed_after_period")
    assert not r.passed

def test_detects_duration_without_start(tmp_path):
    con = _db(tmp_path, [
        _f("a", 1, "revenue", 100.0, "duration",
           None, date(2023, 3, 31), date(2023, 5, 10)),
    ])
    r = next(x for x in run_quality_checks(con) if x.name == "duration_has_start")
    assert not r.passed

def test_detects_instant_with_start(tmp_path):
    con = _db(tmp_path, [
        _f("a", 1, "total_assets", 5.0, "instant",
           date(2023, 1, 1), date(2023, 3, 31), date(2023, 5, 10)),
    ])
    r = next(x for x in run_quality_checks(con) if x.name == "instant_has_no_start")
    assert not r.passed

def test_every_check_reports_threshold(tmp_path):
    con = _db(tmp_path, [
        _f("a", 1, "revenue", 100.0, "duration",
           date(2023, 1, 1), date(2023, 3, 31), date(2023, 5, 10)),
    ])
    for r in run_quality_checks(con):
        assert r.threshold is not None and r.detail
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_quality.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'edgar.quality'`

- [ ] **Step 3: Write checks.py**

```python
# src/edgar/quality/checks.py
from dataclasses import dataclass
import duckdb


@dataclass(frozen=True)
class QualityResult:
    name: str
    passed: bool
    observed: float
    threshold: float
    detail: str


# (name, violating-row SQL, max allowed fraction, rationale)
_CHECKS: tuple[tuple[str, str, float, str], ...] = (
    ("filed_after_period",
     "SELECT count(*) FROM fact WHERE filed_date < period_end",
     0.0,
     "A fact cannot be filed before the period it describes ends. "
     "Any violation means filed_date or period_end is wrong, which would "
     "silently break every as-of query."),
    ("duration_has_start",
     "SELECT count(*) FROM fact WHERE period_type='duration' "
     "AND period_start IS NULL",
     0.0,
     "Duration facts must have a start date, or period length is unknown."),
    ("instant_has_no_start",
     "SELECT count(*) FROM fact WHERE period_type='instant' "
     "AND period_start IS NOT NULL",
     0.0,
     "Instant facts describe a point in time and must not carry a start."),
    ("mapping_rule_present",
     "SELECT count(*) FROM fact WHERE mapping_rule_id IS NULL "
     "OR mapping_rule_id = ''",
     0.0,
     "Every fact must trace to the rule that produced it (spec §5 lineage)."),
    ("value_not_null",
     "SELECT count(*) FROM fact WHERE value IS NULL",
     0.0,
     "Null values must be absent rows, not null facts, so the coverage "
     "map can classify them (spec §4.6)."),
)


def run_quality_checks(con: duckdb.DuckDBPyConnection) -> list[QualityResult]:
    total = con.execute("SELECT count(*) FROM fact").fetchone()[0] or 1
    results = []
    for name, sql, threshold, rationale in _CHECKS:
        violations = con.execute(sql).fetchone()[0]
        observed = violations / total
        results.append(QualityResult(
            name=name,
            passed=observed <= threshold,
            observed=observed,
            threshold=threshold,
            detail=f"{violations} violating rows of {total}. {rationale}",
        ))
    return results
```

- [ ] **Step 4: Write pipeline.py**

```python
# src/edgar/pipeline.py
from pathlib import Path
from edgar.db import connect, init_schema
from edgar.ingest.archives import Quarter, enumerate_quarters, download_archive
from edgar.ingest.extract import extract_archive
from edgar.ingest.load import load_quarter
from edgar.curate.mapping import create_mapping_table, seed_mapping_rules
from edgar.curate.company import build_company_table
from edgar.curate.facts import create_fact_table, build_facts
from edgar.curate.universe import apply_eligibility
from edgar.analysis.restatement import restatement_stats, filing_lag_stats
from edgar.quality.checks import run_quality_checks


def build_all(con, start: Quarter, end: Quarter, raw_dir: Path) -> dict:
    """Full Stage 1 build. Safe to re-run: every step is idempotent."""
    init_schema(con)
    create_mapping_table(con)
    seed_mapping_rules(con)
    create_fact_table(con)

    for q in enumerate_quarters(start, end):
        zip_path = download_archive(q, raw_dir)
        files = extract_archive(zip_path, raw_dir / q.label)
        load_quarter(con, files, q)

    n_companies = build_company_table(con)
    n_facts = build_facts(con)
    eligibility = apply_eligibility(con)

    return {
        "companies": n_companies,
        "facts": n_facts,
        "eligibility": eligibility,
        "restatement": restatement_stats(con),
        "filing_lag": filing_lag_stats(con),
        "quality": [r.__dict__ for r in run_quality_checks(con)],
    }


if __name__ == "__main__":
    import json
    from edgar.config import get_settings
    s = get_settings()
    con = connect(s.duckdb_path)
    report = build_all(
        con,
        Quarter(s.start_year, s.start_quarter),
        Quarter(2026, 2),
        s.raw_dir,
    )
    print(json.dumps(report, indent=2, default=str))
```

- [ ] **Step 5: Write Makefile**

```makefile
.PHONY: install test build clean

install:
	pip install -e ".[dev]"

test:
	pytest -v

build:
	python -m edgar.pipeline

clean:
	rm -rf data/edgar.duckdb
```

- [ ] **Step 6: Run the full test suite**

Run: `pytest -v`
Expected: all tests pass across every test file

- [ ] **Step 7: Commit**

```bash
git add src/edgar/quality src/edgar/pipeline.py Makefile tests/test_quality.py
git commit -m "feat: data quality suite with documented thresholds and pipeline entry point"
```

---

## Task 16: Generated data dictionary and Stage 1 findings

**Files:**
- Create: `src/edgar/quality/dictionary.py`, `docs/data-dictionary.md` (generated), `docs/stage1-findings.md` (written)
- Test: `tests/test_dictionary.py`

**Interfaces:**
- Consumes: `fact`, `mapping_rule`, `company` tables.
- Produces: `generate_data_dictionary(con) -> str` returning Markdown; `FIELD_DEFINITIONS: dict[str, str]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dictionary.py
from edgar.db import connect
from edgar.curate.facts import create_fact_table
from edgar.curate.mapping import create_mapping_table, seed_mapping_rules
from edgar.quality.dictionary import generate_data_dictionary, FIELD_DEFINITIONS
from edgar.curate.mapping import CANONICAL_FIELDS

def test_every_canonical_field_has_a_definition():
    for f in CANONICAL_FIELDS:
        assert f in FIELD_DEFINITIONS
        assert len(FIELD_DEFINITIONS[f]) > 20

def test_dictionary_lists_tables_and_fields(tmp_path):
    con = connect(tmp_path / "t.duckdb")
    create_fact_table(con); create_mapping_table(con); seed_mapping_rules(con)
    md = generate_data_dictionary(con)
    assert "## fact" in md
    assert "filed_date" in md
    assert "revenue" in md
    assert "RevenueFromContractWithCustomerExcludingAssessedTax" in md
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_dictionary.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'edgar.quality.dictionary'`

- [ ] **Step 3: Write dictionary.py**

```python
# src/edgar/quality/dictionary.py
import duckdb
from edgar.curate.mapping import CANONICAL_FIELDS

FIELD_DEFINITIONS: dict[str, str] = {
    "revenue": "Total revenue recognized in the period from contracts with "
               "customers. Duration fact.",
    "cost_of_revenue": "Direct cost attributable to goods and services sold "
                       "in the period. Often not separately disclosed. "
                       "Duration fact.",
    "gross_profit": "Revenue less cost of revenue. Duration fact. Reported "
                    "directly when available; not derived in v1.",
    "operating_income": "Profit from operations before interest and tax. "
                        "Duration fact.",
    "net_income": "Profit after all expenses, interest, and tax, attributable "
                  "to the parent entity. Duration fact.",
    "total_assets": "Total assets held at the balance sheet date. Instant fact.",
    "total_liabilities": "Total obligations owed at the balance sheet date. "
                         "Instant fact.",
    "stockholders_equity": "Residual interest in assets after deducting "
                           "liabilities. Instant fact.",
    "operating_cash_flow": "Net cash generated by operating activities in the "
                           "period. Duration fact.",
    "capex": "Cash paid to acquire property, plant, and equipment in the "
             "period, normalized to a positive magnitude. Duration fact.",
}

_TABLES = ("fact", "company", "mapping_rule")


def generate_data_dictionary(con: duckdb.DuckDBPyConnection) -> str:
    lines = [
        "# Data Dictionary",
        "",
        "Generated from the live schema. Do not edit by hand — "
        "regenerate with `python -m edgar.quality.dictionary`.",
        "",
        "## Canonical fields",
        "",
        "| Field | Definition |",
        "|---|---|",
    ]
    for f in CANONICAL_FIELDS:
        lines.append(f"| `{f}` | {FIELD_DEFINITIONS[f]} |")

    for table in _TABLES:
        cols = con.execute(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_name = ? ORDER BY ordinal_position", [table]
        ).fetchall()
        if not cols:
            continue
        lines += ["", f"## {table}", "", "| Column | Type |", "|---|---|"]
        lines += [f"| `{c}` | {t} |" for c, t in cols]

    rules = con.execute(
        "SELECT canonical_field, source_tag, priority, rationale "
        "FROM mapping_rule ORDER BY canonical_field, priority"
    ).fetchall()
    if rules:
        lines += ["", "## Mapping rules", "",
                  "| Canonical field | Source tag | Priority | Rationale |",
                  "|---|---|---|---|"]
        lines += [f"| `{f}` | `{t}` | {p} | {r} |" for f, t, p, r in rules]

    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    from pathlib import Path
    from edgar.db import connect
    from edgar.config import get_settings
    out = Path("docs/data-dictionary.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(generate_data_dictionary(connect(get_settings().duckdb_path)))
    print(f"wrote {out}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_dictionary.py -v`
Expected: 2 passed

- [ ] **Step 5: Run the real build**

```bash
make build > docs/stage1-build-report.json
python -m edgar.quality.dictionary
```

- [ ] **Step 6: Write the findings document**

Create `docs/stage1-findings.md` reporting, with actual numbers from the build:

1. Companies ingested; eligible after each screening rule (from `eligibility`).
2. Facts built; canonical field coverage rate per field.
3. **Restatement rate**, median and maximum absolute percentage change.
4. **Filing lag**: median and 90th-percentile days from period end to first filing.
5. Mapping coverage: fraction of `raw_num` rows whose tag had a rule; the 20 most frequent unmapped tags.
6. Quality check results, all passing or explained.
7. One worked as-of example: pick the largest restatement found and show the value returned at three different `as_of` dates.

- [ ] **Step 7: Commit**

```bash
git add src/edgar/quality/dictionary.py tests/test_dictionary.py \
        docs/data-dictionary.md docs/stage1-findings.md \
        docs/stage1-build-report.json
git commit -m "feat: generated data dictionary and Stage 1 empirical findings"
```

---

## Stage 1 Definition of Done

- [ ] `pytest -v` passes with no failures
- [ ] `make build` completes on the full 2019Q1–present range
- [ ] All quality checks pass, or each failure is explained in `docs/stage1-findings.md`
- [ ] `docs/verification/dera-format.md` exists and its conclusions match the code
- [ ] `docs/data-dictionary.md` is generated, not hand-written
- [ ] `docs/stage1-findings.md` reports the restatement rate and filing lag with real numbers
- [ ] A worked as-of example demonstrates that the same query at two dates returns two different values

**Gate to Stage 2:** do not begin the agent until the as-of layer is queryable and the findings document exists. Stage 2's entire value rests on this foundation being correct.
