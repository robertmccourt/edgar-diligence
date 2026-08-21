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

