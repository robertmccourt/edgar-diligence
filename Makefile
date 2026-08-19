.PHONY: install test build clean

install:
	pip install -e ".[dev]"

test:
	pytest -v

build:
	python -m edgar.pipeline

clean:
	rm -rf data/edgar.duckdb
