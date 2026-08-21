.PHONY: install test build clean rebuild-curated

install:
	pip install -e ".[dev]"

test:
	pytest -v

build:
	python -m edgar.pipeline

rebuild-curated:
	PYTHONPATH=src venv/bin/python -c "from edgar.db import connect; from edgar.config import get_settings; from edgar.pipeline import rebuild_curated; import json; print(json.dumps(rebuild_curated(connect(get_settings().duckdb_path)), indent=2, default=str))"

clean:
	rm -rf data/edgar.duckdb

narrative:
	venv/bin/pip install -q -e ".[narrative]"
	venv/bin/python scripts/fetch_narratives.py

index:
	PYTHONPATH=src venv/bin/python -c "from edgar.db import connect; from edgar.config import get_settings; from edgar.narrative.store import index_spans; from edgar.narrative.embedder import SentenceTransformerEmbedder; print(index_spans(connect(get_settings().duckdb_path), SentenceTransformerEmbedder()), 'spans indexed')"

langfuse-up:
	@test -d ../langfuse || git clone https://github.com/langfuse/langfuse.git ../langfuse
	cd ../langfuse && docker compose up -d
	@echo "Langfuse at http://localhost:3000 — create an org/project, then put"
	@echo "LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY / LANGFUSE_HOST=http://localhost:3000 in .env"

memo:
	PYTHONPATH=src venv/bin/python -m edgar.agent.run --cik $(CIK) --as-of $(AS_OF)

eval:
	PYTHONPATH=src venv/bin/python -m edgar.eval.run_eval $(MEMO)

adversarial:
	PYTHONPATH=src venv/bin/python -m edgar.eval.adversarial
