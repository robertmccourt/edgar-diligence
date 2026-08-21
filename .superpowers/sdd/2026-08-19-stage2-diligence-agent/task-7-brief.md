### Task 7: Chunking, embedding, hybrid search — `search_filings`

Spec §4.9: chunked with character offsets preserved; hybrid retrieval (BM25 + embeddings) filtered by `filed_date <= as_of`. Embeddings come from a local model behind a protocol; tests use a deterministic fake. BM25 uses DuckDB's FTS extension when loadable, and degrades to embedding-only when not (the extension download needs network once; degradation is logged, never silent).

**Files:**
- Create: `src/edgar/narrative/embedder.py`, extend `src/edgar/narrative/store.py`
- Test: `tests/test_narrative_search.py`
- Modify: `Makefile` (target `index`), `scripts/fetch_narratives.py` (call indexing after fetch)

**Interfaces:**
- Consumes: `narrative_doc`/`span` tables, `SpanDTO` (Task 3).
- Produces:

```python
# edgar.narrative.embedder
EMBED_DIM = 384
class Embedder(Protocol):
    def encode(self, texts: list[str]) -> list[list[float]]: ...
class FakeEmbedder:            # deterministic, offline; for tests
    def encode(self, texts): ...
class SentenceTransformerEmbedder:   # lazy-imports sentence_transformers
    def __init__(self, model_name: str = "all-MiniLM-L6-v2"): ...
    def encode(self, texts): ...

# edgar.narrative.store (additions)
chunk_text(text: str, target: int = 1200, overlap: int = 150) -> list[tuple[int, int]]
index_spans(con, embedder: Embedder) -> int          # chunks+embeds all docs; idempotent
try_load_fts(con) -> bool                             # INSTALL/LOAD fts; False on failure
search_spans(con, query: str, cik: int, as_of: date, k: int = 8,
             embedder: Embedder, items: list[str] | None = None) -> list[SpanDTO]
```

`span_id` = `sha256(f"{doc_id}|{char_start}|{char_end}")[:16]`. `chunk_text` splits on paragraph boundaries (`"\n\n"` else sentence-ish fallback), emitting `(start, end)` offsets into the stored item text such that `text[start:end]` reproduces the chunk exactly. Hybrid ranking: reciprocal-rank fusion, `score(s) = Σ 1/(60 + rank)` over the embedding ranking and (when FTS loaded) the BM25 ranking.

- [ ] **Step 1: Failing tests** — `tests/test_narrative_search.py`:

```python
from datetime import date
from edgar.db import connect
from edgar.narrative.store import (
    create_narrative_tables, chunk_text, index_spans, search_spans)
from edgar.narrative.embedder import FakeEmbedder, EMBED_DIM

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
         filed=date(2024, 10, 1))
    n = index_spans(con, FakeEmbedder())
    assert n > 0
    hits = search_spans(con, "freight costs", cik=1,
                        as_of=date(2023, 12, 31), k=5, embedder=FakeEmbedder())
    assert hits and all(h.filed_date <= date(2023, 12, 31) for h in hits)
    accs = {h.accession for h in hits}
    assert accs == {"a-1"}

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
    _doc(con, "d2", "Discussion of results. " * 40, item="Item 7")
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
```

- [ ] **Step 2: Run to verify failure** — ImportError expected.

- [ ] **Step 3: Implement**

`src/edgar/narrative/embedder.py`:

```python
import hashlib
import math
from typing import Protocol

EMBED_DIM = 384


class Embedder(Protocol):
    def encode(self, texts: list[str]) -> list[list[float]]: ...


class FakeEmbedder:
    """Deterministic bag-of-token-hash vectors. Offline; similarity is
    token overlap, which is exactly enough to test ranking plumbing."""

    def encode(self, texts: list[str]) -> list[list[float]]:
        out = []
        for t in texts:
            v = [0.0] * EMBED_DIM
            for tok in t.lower().split():
                h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
                v[h % EMBED_DIM] += 1.0
            norm = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / norm for x in v])
        return out


class SentenceTransformerEmbedder:
    """all-MiniLM-L6-v2: 384-dim, local, free. Lazy import — the model
    download (~90MB, one-time) happens only on real indexing runs."""

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        from sentence_transformers import SentenceTransformer
        self._model = SentenceTransformer(model_name)

    def encode(self, texts: list[str]) -> list[list[float]]:
        return [list(map(float, v)) for v in
                self._model.encode(texts, normalize_embeddings=True)]
```

Additions to `src/edgar/narrative/store.py`:

```python
import hashlib
from datetime import date
from edgar.narrative.embedder import Embedder, EMBED_DIM
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
    if len(breaks) <= 2 and len(text) > target:     # no paragraphs: fixed windows
        step = target - overlap
        return [(s, min(s + target, len(text)))
                for s in range(0, len(text), step) if text[s:s + target].strip()]
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
    docs = con.execute(
        "SELECT doc_id, cik, accession, form, item, filed_date, text "
        "FROM narrative_doc").fetchall()
    con.execute("DELETE FROM span")          # rebuild = idempotent by content ids
    n = 0
    for doc_id, cik, accn, form, item, filed, text in docs:
        offsets = chunk_text(text)
        pieces = [text[s:e] for s, e in offsets]
        if not pieces:
            continue
        vecs = embedder.encode(pieces)
        rows = []
        for (s, e), piece, vec in zip(offsets, pieces, vecs):
            span_id = hashlib.sha256(f"{doc_id}|{s}|{e}".encode()).hexdigest()[:16]
            rows.append([span_id, doc_id, cik, accn, form, item, filed,
                         s, e, piece, vec])
        con.executemany(
            "INSERT OR REPLACE INTO span VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
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
            ORDER BY array_cosine_similarity(embedding, ?::FLOAT[{EMBED_DIM}]) DESC
            LIMIT ?""",
        [cik, as_of, *params, qvec, k * 4]).fetchall()
    rankings = [[r[0] for r in emb_rows]]
    if try_load_fts(con):
        try:
            bm_rows = con.execute(
                f"""SELECT span_id FROM (
                        SELECT span_id, fts_main_span.match_bm25(span_id, ?) AS s
                        FROM span WHERE cik = ? AND filed_date <= ? {item_pred})
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
            WHERE span_id IN ({', '.join('?' for _ in top)})""", top).fetchall()
    by_id = {r[0]: r for r in rows}
    return [SpanDTO(span_id=r[0], cik=r[1], accession=r[2], form=r[3],
                    item=r[4], filed_date=r[5], char_start=r[6],
                    char_end=r[7], text=r[8])
            for r in (by_id[sid] for sid in top if sid in by_id)]
```

Makefile target `index` (real store, real model):

```make
index:
	venv/bin/python -c "from edgar.db import connect; from edgar.config import get_settings; from edgar.narrative.store import index_spans; from edgar.narrative.embedder import SentenceTransformerEmbedder; print(index_spans(connect(get_settings().duckdb_path), SentenceTransformerEmbedder()), 'spans indexed')"
```

- [ ] **Step 4: Run** — `venv/bin/pytest tests/test_narrative_search.py -q` → PASS; full suite. (If the FTS `INSTALL` fails offline, tests still pass — the design degrades to embedding-only; confirm no test depends on BM25 specifically.)

- [ ] **Step 5: Index the real store** — `make index` (first run downloads the MiniLM model). Sanity: search AAPL MD&A for a phrase you can see on sec.gov and confirm the hit resolves (`text[char_start:char_end] == text`).

- [ ] **Step 6: Commit**

```bash
git add src/edgar/narrative tests/test_narrative_search.py Makefile
git commit -m "feat(narrative): offset-preserving chunks, hybrid as-of search

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

