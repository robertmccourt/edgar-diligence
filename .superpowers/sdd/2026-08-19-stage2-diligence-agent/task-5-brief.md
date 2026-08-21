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

