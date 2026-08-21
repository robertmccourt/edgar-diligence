"""Point-in-time visibility, split from derivation-integrity checking.

Two independent questions get conflated if answered by one function:

  1. Was this identifier knowable on `as_of`? (a date comparison only)
  2. Does this derivation still recompute to its stored value? (an
     integrity check that has nothing to do with time travel)

`visible_asof` answers only the first, by design: it is the function the
eval's `temporal_leakage` gate uses, and that gate must stay a pure
point-in-time signal — a broken derivation is a correctness bug, not a
leak. `check_integrity` answers only the second, and is None for fact/span
ids (they have no recompute step) and for unknown derivation ids (nothing
to recompute).
"""
from datetime import date

import duckdb

from edgar.tools.compute import ComputeError, recompute


def visible_asof(con: duckdb.DuckDBPyConnection, cid: str,
                 as_of: date) -> str | None:
    """Return None when `cid` was knowable on `as_of`, else a human-
    readable problem. Date comparison only — fact/span `filed_date`,
    derivation `as_of` column. Never recomputes."""
    if cid.startswith("D-"):
        row = con.execute(
            "SELECT as_of FROM derivation WHERE derivation_id = ?",
            [cid]).fetchone()
        if row is None:
            return f"unknown derivation {cid}"
        return None if row[0] <= as_of else \
            f"derivation {cid} computed for later as_of {row[0]}"
    for table, col in (("fact", "fact_id"), ("span", "span_id")):
        row = con.execute(
            f"SELECT filed_date FROM {table} WHERE {col} = ?",
            [cid]).fetchone()
        if row is not None:
            return None if row[0] <= as_of else \
                f"{table} {cid} filed {row[0]} after as_of {as_of}"
    return f"unknown identifier {cid}"


def check_integrity(con: duckdb.DuckDBPyConnection,
                    cid: str) -> str | None:
    """Derivation-recompute check only. Always None for fact/span ids and
    for an unknown derivation id (that is `visible_asof`'s problem to
    report, not this function's)."""
    if not cid.startswith("D-"):
        return None
    row = con.execute(
        "SELECT value FROM derivation WHERE derivation_id = ?",
        [cid]).fetchone()
    if row is None:
        return None
    try:
        again = recompute(con, cid)
    except ComputeError as exc:
        return f"derivation {cid} does not recompute: {exc}"
    if abs(again.value - row[0]) > 1e-9 * max(1.0, abs(row[0])):
        return (f"derivation {cid} stored {row[0]} but recomputes "
                f"to {again.value}")
    return None
