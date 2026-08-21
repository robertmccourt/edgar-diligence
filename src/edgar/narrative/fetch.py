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
    """Every accession should carry all three Items at plausible length.

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
    """Real fetcher. Never used by tests (which inject fakes); the real
    pull runs via scripts/fetch_narratives.py OUTSIDE pythonpath=src,
    because the edgartools package is also imported as `edgar` and
    collides with this project's package. See the script's docstring."""
    raise RuntimeError(
        "edgartools_fetcher must run via scripts/fetch_narratives.py "
        "(package-name collision: edgartools is also imported as 'edgar')")
