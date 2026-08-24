import os
from functools import lru_cache
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

_SECRET_KEYS = ("ANTHROPIC_API_KEY", "OPENROUTER_API_KEY",
                "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY",
                "LANGFUSE_HOST")


class Settings(BaseSettings):
    # extra="ignore": .env also holds unprefixed secrets (API keys) read by
    # load_secrets_env, which pydantic-settings would otherwise reject as
    # extra inputs the moment one is uncommented.
    model_config = SettingsConfigDict(env_prefix="EDGAR_", env_file=".env",
                                      extra="ignore")

    data_dir: Path = Path("data")
    duckdb_path: Path = Path("data/edgar.duckdb")
    sec_user_agent: str = "Robert McCourt rmmccourt01@comcast.net"
    start_year: int = 2019
    start_quarter: int = 1
    generation_model: str = "claude-opus-5"
    judge_model: str = "claude-sonnet-5"
    # Fixed Stage 2 narrative/eval set (spec §4.9): eligible, sector- and
    # fiscal-calendar-diverse, all 10 v1 fields present. Verified 2026-08-19.
    narrative_ciks: tuple[int, ...] = (
        320193, 789019, 1045810, 1318605, 77476,
        200406, 354950, 909832, 1018724, 1652044,
    )

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"


def load_secrets_env(path: Path | None = None) -> list[str]:
    """os.environ.setdefault unprefixed secrets from .env.

    pydantic-settings binds only EDGAR_-prefixed keys; the anthropic and
    langfuse SDKs read their own env vars. Shell env always wins.
    """
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


@lru_cache
def get_settings() -> Settings:
    return Settings()
