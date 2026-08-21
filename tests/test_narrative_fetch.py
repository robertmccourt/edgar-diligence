from datetime import date

from edgar.db import connect
from edgar.narrative.store import create_narrative_tables
from edgar.narrative.fetch import FilingDoc, fetch_narratives, verify_store


def _fake_fetcher(cik, n):
    return [FilingDoc(cik=cik, accession=f"acc-{cik}-{i}", form="10-K",
                      filed_date=date(2024 - i, 10, 1),
                      fiscal_year=str(2024 - i),
                      items={"Item 1": "We sell widgets. " * 60,
                             "Item 1A": "Risks include competition. " * 60,
                             "Item 7": "Revenue grew due to pricing. " * 60})
            for i in range(n)]


def test_fetch_stores_one_row_per_item(tmp_path):
    con = connect(tmp_path / "t.duckdb")
    create_narrative_tables(con)
    report = fetch_narratives(con, [1, 2], per_company=2,
                              fetcher=_fake_fetcher)
    n = con.execute("SELECT count(*) FROM narrative_doc").fetchone()[0]
    assert n == 2 * 2 * 3           # ciks x filings x items
    assert report["docs"] == 12 and report["companies"] == 2
    assert verify_store(con) == []


def test_fetch_is_idempotent(tmp_path):
    con = connect(tmp_path / "t.duckdb")
    create_narrative_tables(con)
    fetch_narratives(con, [1], per_company=1, fetcher=_fake_fetcher)
    fetch_narratives(con, [1], per_company=1, fetcher=_fake_fetcher)
    n = con.execute("SELECT count(*) FROM narrative_doc").fetchone()[0]
    assert n == 3


def test_verify_flags_short_and_missing_items(tmp_path):
    con = connect(tmp_path / "t.duckdb")
    create_narrative_tables(con)

    def bad_fetcher(cik, n):
        return [FilingDoc(cik=cik, accession="a", form="10-K",
                          filed_date=date(2024, 10, 1), fiscal_year="2024",
                          items={"Item 1": "too short"})]
    fetch_narratives(con, [1], per_company=1, fetcher=bad_fetcher)
    problems = verify_store(con)
    assert any("short" in p for p in problems)
    assert any("Item 7" in p for p in problems)
