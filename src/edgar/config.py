from functools import lru_cache
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EDGAR_", env_file=".env")

    data_dir: Path = Path("data")
    duckdb_path: Path = Path("data/edgar.duckdb")
    sec_user_agent: str = "Robert McCourt rmmccourt01@comcast.net"
    start_year: int = 2019
    start_quarter: int = 1

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"


@lru_cache
def get_settings() -> Settings:
    return Settings()
