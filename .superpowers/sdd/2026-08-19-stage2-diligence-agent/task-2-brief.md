### Task 2: Settings, secrets loader, dependency pins

**Files:**
- Modify: `src/edgar/config.py`
- Modify: `pyproject.toml`
- Test: `tests/test_config.py` (new)

**Interfaces:**
- Consumes: existing `Settings` / `get_settings()`.
- Produces (exact names later tasks import):
  - `Settings.generation_model: str = "claude-opus-5"`
  - `Settings.judge_model: str = "claude-sonnet-5"`
  - `Settings.narrative_ciks: tuple[int, ...]` — default `(320193, 789019, 1045810, 1318605, 77476, 200406, 354950, 909832, 1018724, 1652044)` (AAPL, MSFT, NVDA, TSLA, PEP, JNJ, HD, COST, AMZN, GOOGL — all eligible, sector- and fiscal-calendar-diverse, verified against the store 2026-08-19)
  - `load_secrets_env(path: Path | None = None) -> list[str]` — parses `.env` for `ANTHROPIC_API_KEY`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST` and `os.environ.setdefault`s each; returns names it set. (pydantic-settings only binds `EDGAR_`-prefixed keys; the anthropic and langfuse SDKs read their own env vars directly.)

- [ ] **Step 1: Failing tests** — `tests/test_config.py`:

```python
import os
from edgar.config import Settings, load_secrets_env

def test_model_defaults():
    s = Settings(_env_file=None)
    assert s.generation_model == "claude-opus-5"
    assert s.judge_model == "claude-sonnet-5"
    assert len(s.narrative_ciks) == 10 and 320193 in s.narrative_ciks

def test_load_secrets_env_sets_and_reports(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    env = tmp_path / ".env"
    env.write_text("ANTHROPIC_API_KEY=sk-test\n# comment\nEDGAR_DATA_DIR=x\n")
    set_names = load_secrets_env(env)
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-test"
    assert set_names == ["ANTHROPIC_API_KEY"]

def test_load_secrets_env_never_overrides(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-shell")
    env = tmp_path / ".env"
    env.write_text("ANTHROPIC_API_KEY=sk-file\n")
    assert load_secrets_env(env) == []
    assert os.environ["ANTHROPIC_API_KEY"] == "from-shell"

def test_load_secrets_env_missing_file_is_noop(tmp_path):
    assert load_secrets_env(tmp_path / "absent.env") == []
```

- [ ] **Step 2: Run to verify failure**

Run: `venv/bin/pytest tests/test_config.py -v` — Expected: FAIL (`AttributeError` / `ImportError`).

- [ ] **Step 3: Implement** — in `src/edgar/config.py` add fields and function:

```python
_SECRET_KEYS = ("ANTHROPIC_API_KEY", "LANGFUSE_PUBLIC_KEY",
                "LANGFUSE_SECRET_KEY", "LANGFUSE_HOST")


class Settings(BaseSettings):
    ...  # existing fields unchanged
    generation_model: str = "claude-opus-5"
    judge_model: str = "claude-sonnet-5"
    # Fixed Stage 2 narrative/eval set (spec §4.9): eligible, sector- and
    # fiscal-calendar-diverse, all 10 v1 fields present. Verified 2026-08-19.
    narrative_ciks: tuple[int, ...] = (
        320193, 789019, 1045810, 1318605, 77476,
        200406, 354950, 909832, 1018724, 1652044,
    )


def load_secrets_env(path: Path | None = None) -> list[str]:
    """os.environ.setdefault unprefixed secrets from .env.

    pydantic-settings binds only EDGAR_-prefixed keys; the anthropic and
    langfuse SDKs read their own env vars. Shell env always wins.
    """
    import os
    target = path if path is not None else Path(".env")
    if not target.exists():
        return []
    loaded: list[str] = []
    for line in target.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key in _SECRET_KEYS and key not in os.environ:
            os.environ[key] = value.strip()
            loaded.append(key)
    return loaded
```

In `pyproject.toml`: remove the unused `pandas>=2.2` from `dependencies` (declared but imported nowhere — verified by grep); add `"langgraph>=1.2,<2"`, `"anthropic>=0.125"`, `"pyyaml>=6"`, `"langfuse>=4,<5"`; add optional group:

```toml
[project.optional-dependencies]
narrative = ["edgartools>=5.51", "sentence-transformers>=6"]
```

(keep the existing `dev` extra as is). Then `venv/bin/pip install -e ".[dev]"`.

- [ ] **Step 4: Run** — `venv/bin/pytest tests/test_config.py -q` then full `venv/bin/pytest -q`. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/edgar/config.py pyproject.toml tests/test_config.py
git commit -m "feat(config): model ids, narrative set, secrets loader; drop unused pandas

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

