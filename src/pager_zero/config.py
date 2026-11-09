"""Runtime configuration. Everything comes from environment / .env — nothing is hard-coded.

Provider-agnostic by design (see notes D-3): the LLM is chosen by LLM_PROVIDER + LLM_MODEL,
the data backend by ADAPTER_BACKEND. Swapping either is a config change, not a code change.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Export .env into the process environment too: provider SDKs (anthropic, google, groq…)
# read their API keys from os.environ, not from our Settings object.
load_dotenv()


class AdapterBackend(StrEnum):
    synthetic = "synthetic"
    live = "live"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- LLM (provider-agnostic) ---
    llm_provider: str = Field(
        "anthropic", description="LangChain provider id: anthropic|google_genai|groq|ollama|openai"
    )
    llm_model: str = Field("claude-opus-5-5", description="Model id understood by the provider")
    llm_temperature: float = 0.0

    # --- agent limits ---
    max_steps: int = Field(12, description="Hard cap on investigate-loop tool calls per incident")

    # --- data backend ---
    adapter_backend: AdapterBackend = AdapterBackend.synthetic

    # --- live adapter endpoints (used only when adapter_backend=live) ---
    prometheus_url: str = "http://localhost:9090"
    kafka_bootstrap: str = "localhost:9092"
    redis_url: str = "redis://localhost:6379/0"
    github_token: str | None = None

    # --- storage ---
    database_url: str = "postgresql+psycopg://pz:pz@localhost:5432/pagerzero"
    checkpoint_db: Path = Path(".pz/checkpoints.sqlite")
    trace_dir: Path = Path(".pz/traces")
    embedding_model: str = "all-MiniLM-L6-v2"


settings = Settings()
