"""Application settings, loaded from environment variables / the project .env file.

Secrets (API keys, access tokens) are NOT settings - they live in the OS keyring (see fin.secrets).
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _default_data_dir() -> Path:
    # Outside the repo on purpose: the database and logs never end up in git.
    return Path.home() / ".finintel"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_prefix="FIN_",
        extra="ignore",
    )

    data_dir: Path = Field(default_factory=_default_data_dir)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    plaid_env: Literal["sandbox", "production"] = "sandbox"
    sec_user_agent: str = ""
    # Extra host names allowed to open the dashboard, comma-separated - e.g. your private
    # Tailscale address "mylaptop.tail1234.ts.net". Never a public address.
    allowed_hosts: str = ""

    @property
    def db_path(self) -> Path:
        # Sandbox (fake) data goes to its own file so it can never mix with real accounts.
        name = "fin-sandbox.db" if self.plaid_env == "sandbox" else "fin.db"
        return self.data_dir / name

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logs"


@lru_cache
def get_settings() -> Settings:
    return Settings()
