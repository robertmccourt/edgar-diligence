.PHONY: install test build clean rebuild-curated

install:
	pip install -e ".[dev]"

test:
	pytest -v

build:
	python -m edgar.pipeline

rebuild-curated:
	venv/bin/python -c "from edgar.db import connect; from edgar.config import get_settings; from edgar.pipeline import rebuild_curated; import json; print(json.dumps(rebuild_curated(connect(get_settings().duckdb_path)), indent=2, default=str))"

clean:
	rm -rf data/edgar.duckdb

narrative:
	venv/bin/pip install -q -e ".[narrative]"
	venv/bin/python scripts/fetch_narratives.py

index:
	venv/bin/python -c "from edgar.db import connect; from edgar.config import get_settings; from edgar.narrative.store import index_spans; from edgar.narrative.embedder import SentenceTransformerEmbedder; print(index_spans(connect(get_settings().duckdb_path), SentenceTransformerEmbedder()), 'spans indexed')"
