"""Central configuration. All secrets/tunables come from .env — nowhere else.

The LLM key is **optional** here, deliberately. Process Lab's engine, the scenario service and
all ten analyst presets run with no credentials at all; only the assistant tab and free-text
analyst questions need a provider. Making the key required at import time (the previous
behaviour) meant any module that merely wanted REPO_ROOT had to avoid touching Settings.
Call require_llm_key() at the point of use instead.
"""
from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = REPO_ROOT / ".env"


class MissingCredential(RuntimeError):
    """Raised when an LLM-backed feature is used without a configured provider key."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    deepseek_api_key: SecretStr | None = Field(default=None)
    deepseek_model: str = Field(default="deepseek-chat")

    langsmith_tracing: bool = Field(default=False)
    langsmith_api_key: SecretStr | None = Field(default=None)
    langsmith_project: str = Field(default="processlab")

    host: str = Field(default="127.0.0.1")
    port: int = Field(default=8000)

    # Scenario/assistant safety caps.
    max_concurrent_jobs: int = Field(default=1, ge=1, le=8)
    max_clarification_turns: int = Field(default=5, ge=1, le=20)
    experiment_timeout_seconds: int = Field(default=900, ge=30)

    @property
    def raw_data_dir(self) -> Path:
        return REPO_ROOT / "raw_data"

    @property
    def runs_dir(self) -> Path:
        d = REPO_ROOT / "runs"
        d.mkdir(exist_ok=True)
        return d

    @property
    def has_llm_key(self) -> bool:
        return bool(self.deepseek_api_key and self.deepseek_api_key.get_secret_value().strip())


@lru_cache
def get_settings() -> Settings:
    return Settings()


def require_llm_key() -> Settings:
    """For the assistant and the free-text analyst planner only."""
    settings = get_settings()
    if not settings.has_llm_key:
        raise MissingCredential(
            "This feature needs a DeepSeek API key in .env (deepseek_api_key). "
            "Process Lab's scenarios and the suggested analyst questions work without one."
        )
    return settings
