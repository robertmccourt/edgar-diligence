# Task 2 Report: Settings, secrets loader, dependency pins

## Status
DONE

## Changes per file

### `src/edgar/config.py`
- Added `_SECRET_KEYS` constant tuple: `("ANTHROPIC_API_KEY", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_HOST")`
- Extended `Settings` class with three new fields:
  - `generation_model: str = "claude-opus-5"`
  - `judge_model: str = "claude-sonnet-5"`
  - `narrative_ciks: tuple[int, ...]` = (320193, 789019, 1045810, 1318605, 77476, 200406, 354950, 909832, 1018724, 1652044)
- Added `load_secrets_env(path: Path | None = None) -> list[str]` function:
  - Parses `.env` file (or supplied path)
  - Loads unprefixed secrets (ANTHROPIC_API_KEY, LANGFUSE_* keys)
  - Uses `os.environ.setdefault()` to never override shell environment
  - Returns list of keys actually set
  - Gracefully handles missing files (returns empty list)

### `pyproject.toml`
- Removed unused `pandas>=2.2` from dependencies
- Added new core dependencies:
  - `langgraph>=1.2,<2`
  - `anthropic>=0.125`
  - `pyyaml>=6`
  - `langfuse>=4,<5`
- Added optional group `narrative`:
  - `edgartools>=5.51`
  - `sentence-transformers>=6`

### `tests/test_config.py` (new)
- Created test file with 4 tests:
  - `test_model_defaults()` — verifies Settings fields have correct defaults
  - `test_load_secrets_env_sets_and_reports()` — verifies function sets env vars and reports them
  - `test_load_secrets_env_never_overrides()` — verifies shell env takes precedence
  - `test_load_secrets_env_missing_file_is_noop()` — verifies missing file is gracefully handled

## Test results

### Config tests (4/4 PASS)
```
tests/test_config.py::test_model_defaults PASSED                         [ 25%]
tests/test_config.py::test_load_secrets_env_sets_and_reports PASSED      [ 50%]
tests/test_config.py::test_load_secrets_env_never_overrides PASSED       [ 75%]
tests/test_config.py::test_load_secrets_env_missing_file_is_noop PASSED  [100%]
```

### Full test suite (146/146 PASS)
```
146 passed in 4.57s
```

## pip install outcome
```
Successfully installed: zstandard, xxhash, wrapt, websockets, uuid-utils, urllib3, truststore, tenacity, sniffio, pyyaml, protobuf, ormsgpack, orjson, opentelemetry-api, langchain-protocol, jsonpointer, jiter, docstring-parser, distro, charset_normalizer, backoff, requests, opentelemetry-semantic-conventions, opentelemetry-proto, jsonpatch, httpcore2, googleapis-common-protos, requests-toolbelt, opentelemetry-sdk, opentelemetry-exporter-otlp-proto-common, httpx2, opentelemetry-exporter-otlp-proto-http, langsmith, anthropic, langfuse, langchain-core, langgraph-sdk, langgraph-checkpoint, langgraph-prebuilt, langgraph, edgar-diligence
```

All new package pins resolved successfully; existing dependencies remain compatible.

## Commit
- Hash: `38953a1`
- Message: `feat(config): model ids, narrative set, secrets loader; drop unused pandas`

## Deviations
None. All requirements from the brief were implemented exactly as specified.
