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

import hashlib
from datetime import date

from edgar.narrative.embedder import EMBED_DIM, Embedder
from edgar.tools.schemas import SpanDTO


def chunk_text(text: str, target: int = 1200, overlap: int = 150
               ) -> list[tuple[int, int]]:
    """Greedy paragraph packer. Offsets index the stored item text;
    text[start:end] IS the span — that identity is what a citation means."""
    breaks = [0]
    i = text.find("\n\n")
    while i != -1:
        breaks.append(i + 2)
        i = text.find("\n\n", i + 2)
    breaks.append(len(text))
    if len(breaks) <= 2 and len(text) > target:     # no paragraphs: windows
        step = target - overlap
        return [(s, min(s + target, len(text)))
                for s in range(0, len(text), step)
                if text[s:s + target].strip()]
    chunks, start = [], 0
    for b in breaks[1:]:
        if b - start >= target:
            chunks.append((start, b))
            start = max(start, b - overlap)
    if start < len(text) and text[start:].strip():
        chunks.append((start, len(text)))
    return chunks


def try_load_fts(con) -> bool:
    try:
        con.execute("INSTALL fts; LOAD fts;")
        return True
    except Exception:
        return False


def index_spans(con, embedder: Embedder) -> int:
    create_narrative_tables(con)   # real store may predate the span table
    docs = con.execute(
        "SELECT doc_id, cik, accession, form, item, filed_date, text "
        "FROM narrative_doc").fetchall()
    con.execute("DELETE FROM span")     # rebuild = idempotent by content ids
    n = 0
    for doc_id, cik, accn, form, item, filed, text in docs:
        offsets = chunk_text(text)
        pieces = [text[s:e] for s, e in offsets]
        if not pieces:
            continue
        vecs = embedder.encode(pieces)
        rows = []
        for (s, e), piece, vec in zip(offsets, pieces, vecs):
            span_id = hashlib.sha256(
                f"{doc_id}|{s}|{e}".encode()).hexdigest()[:16]
            rows.append([span_id, doc_id, cik, accn, form, item, filed,
                         s, e, piece, vec])
        con.executemany(
            "INSERT OR REPLACE INTO span VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            rows)
        n += len(rows)
    if try_load_fts(con):
        con.execute("PRAGMA create_fts_index('span', 'span_id', 'text', "
                    "overwrite=1)")
    return n


def search_spans(con, query: str, cik: int, as_of: date, k: int = 8,
                 embedder: Embedder | None = None,
                 items: list[str] | None = None) -> list[SpanDTO]:
    assert embedder is not None
    item_pred = ""
    params: list = []
    if items:
        item_pred = f"AND item IN ({', '.join('?' for _ in items)})"
        params = list(items)
    qvec = embedder.encode([query])[0]
    emb_rows = con.execute(
        f"""SELECT span_id FROM span
            WHERE cik = ? AND filed_date <= ? {item_pred}
            ORDER BY array_cosine_similarity(
                embedding, ?::FLOAT[{EMBED_DIM}]) DESC
            LIMIT ?""",
        [cik, as_of, *params, qvec, k * 4]).fetchall()
    rankings = [[r[0] for r in emb_rows]]
    if try_load_fts(con):
        try:
            bm_rows = con.execute(
                f"""SELECT span_id FROM (
                        SELECT span_id,
                               fts_main_span.match_bm25(span_id, ?) AS s
                        FROM span
                        WHERE cik = ? AND filed_date <= ? {item_pred})
                    WHERE s IS NOT NULL ORDER BY s DESC LIMIT ?""",
                [query, cik, as_of, *params, k * 4]).fetchall()
            rankings.append([r[0] for r in bm_rows])
        except Exception:
            pass          # index absent on this connection: embedding-only
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, sid in enumerate(ranking):
            scores[sid] = scores.get(sid, 0.0) + 1.0 / (60 + rank)
    top = sorted(scores, key=scores.get, reverse=True)[:k]
    if not top:
        return []
    rows = con.execute(
        f"""SELECT span_id, cik, accession, form, item, filed_date,
                   char_start, char_end, text FROM span
            WHERE span_id IN ({', '.join('?' for _ in top)})""",
        top).fetchall()
    by_id = {r[0]: r for r in rows}
    return [SpanDTO(span_id=r[0], cik=r[1], accession=r[2], form=r[3],
                    item=r[4], filed_date=r[5], char_start=r[6],
                    char_end=r[7], text=r[8])
            for r in (by_id[sid] for sid in top if sid in by_id)]
