"""Fetch 10-K Items 1 / 1A / 7 for the narrative company set via edgartools.

Runs OUTSIDE pythonpath=src so `import edgar` resolves to the edgartools
package (name collision with this project's src/edgar). Uses edgartools
only — no import of the project package. Config is read directly from
.env / environment: EDGAR_DUCKDB_PATH, EDGAR_SEC_USER_AGENT.

Usage: venv/bin/python scripts/fetch_narratives.py
"""
import hashlib
import os
import sys
from pathlib import Path

sys.path = [p for p in sys.path if not p.rstrip("/").endswith("/src")]

NARRATIVE_CIKS = [320193, 789019, 1045810, 1318605, 77476,
                  200406, 354950, 909832, 1018724, 1652044]
ITEMS = ("Item 1", "Item 1A", "Item 7")
PER_COMPANY = 4


def _env(key: str, default: str) -> str:
    if key in os.environ:
        return os.environ[key]
    env_file = Path(".env")
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line.startswith(f"{key}="):
                return line.partition("=")[2].strip()
    return default


def main() -> None:
    import duckdb
    from edgar import Company, set_identity          # edgartools

    set_identity(_env("EDGAR_SEC_USER_AGENT",
                      "Robert McCourt rmmccourt01@comcast.net"))
    db_path = _env("EDGAR_DUCKDB_PATH", "data/edgar.duckdb")
    con = duckdb.connect(db_path)
    con.execute("""
        CREATE TABLE IF NOT EXISTS narrative_doc (
            doc_id VARCHAR PRIMARY KEY,
            cik BIGINT, accession VARCHAR, form VARCHAR, filed_date DATE,
            fiscal_year VARCHAR, item VARCHAR, text VARCHAR)""")
    total = 0
    for cik in NARRATIVE_CIKS:
        filings = Company(cik).get_filings(form="10-K").head(PER_COMPANY)
        for f in filings:
            tenk = f.obj()
            for item in ITEMS:
                try:
                    text = tenk[item]
                except (KeyError, TypeError):
                    text = None
                if not text:
                    print(f"WARN cik={cik} {f.accession_no}: {item} empty")
                    continue
                doc_id = hashlib.sha256(
                    f"{f.accession_no}|{item}".encode()).hexdigest()[:16]
                con.execute(
                    "INSERT OR REPLACE INTO narrative_doc VALUES "
                    "(?,?,?,?,?,?,?,?)",
                    [doc_id, cik, f.accession_no, "10-K", f.filing_date,
                     str(getattr(f, "fiscal_year", "") or
                         f.filing_date.year), item, str(text)])
                total += 1
        print(f"cik {cik}: done")
    print(f"{total} item-documents stored in {db_path}")


if __name__ == "__main__":
    main()
