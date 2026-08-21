from datetime import date

from edgar.db import connect
from edgar.narrative.embedder import EMBED_DIM, FakeEmbedder
from edgar.narrative.store import (
    chunk_text, create_narrative_tables, index_spans, search_spans)


def _doc(con, doc_id, text, cik=1, accn="a-1", item="Item 7",
         filed=date(2023, 10, 1)):
    con.execute("INSERT INTO narrative_doc VALUES (?,?,?,?,?,?,?,?)",
                [doc_id, cik, accn, "10-K", filed, "2023", item, text])


def test_chunk_offsets_roundtrip():
    text = ("Alpha paragraph. " * 30 + "\n\n") * 5
    chunks = chunk_text(text)
    assert chunks, "no chunks produced"
    for s, e in chunks:
        assert 0 <= s < e <= len(text)
        assert text[s:e].strip()


def test_fake_embedder_is_deterministic_and_sized():
    e = FakeEmbedder()
    a, b = e.encode(["hello"]), e.encode(["hello"])
    assert a == b and len(a[0]) == EMBED_DIM


def test_index_and_search_respects_as_of(tmp_path):
    con = connect(tmp_path / "t.duckdb")
    create_narrative_tables(con)
    _doc(con, "d1", "Freight costs pressured gross margin this year. " * 40,
         filed=date(2023, 10, 1))
    _doc(con, "d2", "Freight costs normalized in the following year. " * 40,
         cik=1, accn="a-2", filed=date(2024, 10, 1))
    n = index_spans(con, FakeEmbedder())
    assert n > 0
    hits = search_spans(con, "freight costs", cik=1,
                        as_of=date(2023, 12, 31), k=5,
                        embedder=FakeEmbedder())
    assert hits and all(h.filed_date <= date(2023, 12, 31) for h in hits)
    assert {h.accession for h in hits} == {"a-1"}


def test_span_text_matches_offsets(tmp_path):
    con = connect(tmp_path / "t.duckdb")
    create_narrative_tables(con)
    body = "Working capital consumed cash due to inventory build. " * 40
    _doc(con, "d1", body)
    index_spans(con, FakeEmbedder())
    h = search_spans(con, "inventory build", cik=1, as_of=date(2024, 1, 1),
                     k=1, embedder=FakeEmbedder())[0]
    assert body[h.char_start:h.char_end] == h.text


def test_item_filter(tmp_path):
    con = connect(tmp_path / "t.duckdb")
    create_narrative_tables(con)
    _doc(con, "d1", "Risky business risks. " * 40, item="Item 1A")
    _doc(con, "d2", "Discussion of results. " * 40, accn="a-2",
         item="Item 7")
    index_spans(con, FakeEmbedder())
    hits = search_spans(con, "risks", cik=1, as_of=date(2024, 1, 1), k=5,
                        embedder=FakeEmbedder(), items=["Item 1A"])
    assert hits and all(h.item == "Item 1A" for h in hits)


def test_index_is_idempotent(tmp_path):
    con = connect(tmp_path / "t.duckdb")
    create_narrative_tables(con)
    _doc(con, "d1", "Some narrative text here. " * 40)
    index_spans(con, FakeEmbedder())
    n1 = con.execute("SELECT count(*) FROM span").fetchone()[0]
    index_spans(con, FakeEmbedder())
    assert con.execute("SELECT count(*) FROM span").fetchone()[0] == n1
