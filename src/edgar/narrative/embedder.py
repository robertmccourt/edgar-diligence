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
