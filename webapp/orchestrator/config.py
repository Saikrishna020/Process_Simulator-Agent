"""Central configuration. All secrets/tunables come from .env — nowhere else."""
from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = REPO_ROOT / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    deepseek_api_key: SecretStr = Field(..., description="Required: DeepSeek API key")
    deepseek_model: str = Field(default="deepseek-chat")

    langsmith_tracing: bool = Field(default=False)
    langsmith_api_key: SecretStr | None = Field(default=None)
    langsmith_project: str = Field(default="agentsimulator-orchestrator")

    orchestrator_host: str = Field(default="127.0.0.1")
    orchestrator_port: int = Field(default=8000)

    max_num_simulations: int = Field(default=20, ge=1, le=100)
    max_concurrent_jobs: int = Field(default=1, ge=1, le=8)
    simulation_timeout_seconds: int = Field(default=7200, ge=30)
    max_run_retries: int = Field(default=2, ge=0, le=5)
    max_clarification_turns: int = Field(default=5, ge=1, le=20)

    @property
    def raw_data_dir(self) -> Path:
        return REPO_ROOT / "raw_data"

    @property
    def simulated_data_dir(self) -> Path:
        return REPO_ROOT / "simulated_data"

    @property
    def runs_dir(self) -> Path:
        d = REPO_ROOT / "runs"
        d.mkdir(exist_ok=True)
        return d


@lru_cache
def get_settings() -> Settings:
    try:
        return Settings()
    except Exception as exc:  # pydantic ValidationError on missing required keys
        raise RuntimeError(
            "Missing/invalid configuration. Copy .env.example to .env and set at least "
            "deepseek_api_key."
        ) from exc
