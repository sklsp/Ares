"""Application configuration.

All settings are environment driven (12-factor). Nothing secret is ever
hardcoded here - defaults are only provided for values that are safe to
publish (local URLs, limits, log levels).
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Database -------------------------------------------------------
    # Postgres is the target database (docker compose provides one). The
    # SQLite default keeps `pytest` and a quick local run dependency free.
    database_url: str = "sqlite:///./ecommerce_agent.db"
    db_echo: bool = False

    # --- LLM ------------------------------------------------------------
    llm_provider: str = "ollama"  # "ollama" | "openai_compatible"
    llm_timeout_seconds: float = 120.0

    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.2"

    openai_base_url: str = "https://api.openai.com/v1"
    openai_api_key: str | None = None
    openai_model: str = "gpt-4o-mini"

    # --- Agent limits ---------------------------------------------------
    agent_max_iterations: int = 8
    agent_max_tool_calls: int = 20
    agent_timeout_seconds: int = 240
    agent_max_workers: int = 4
    # When true, POST /agent/run executes the agent inline instead of on a
    # background worker. Used by the test suite for deterministic runs.
    agent_run_inline: bool = False

    # --- HTTP -----------------------------------------------------------
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    log_level: str = "INFO"
    # When set, every API request must present this value in X-API-Key.
    # Leave empty for open local development.
    api_key: str | None = None

    # --- Intelligence crawling ------------------------------------------
    # SSRF guard: private/loopback destinations are blocked unless this is
    # explicitly enabled for local development fixtures.
    crawler_allow_private_addresses: bool = False

    # --- Background jobs -------------------------------------------------
    # Local development: the API runs an embedded worker. Production: run
    # `python -m app.worker` replicas and set EMBEDDED_WORKER=false here.
    embedded_worker: bool = True

    # --- Rate limiting ---------------------------------------------------
    rate_limit_disabled: bool = False
    rate_limit_agent_per_minute: int = 10
    rate_limit_research_per_minute: int = 10
    rate_limit_login_per_minute: int = 20
    # When set, rate limits are shared across API replicas via Redis.
    # Leave empty for single-process local development (in-process limiter).
    redis_url: str | None = None

    # --- Observability ---------------------------------------------------
    # Tracing is optional: with OTEL_ENABLED=false the app runs identically
    # with no collector. Set OTEL_EXPORTER_OTLP_ENDPOINT in production.
    otel_enabled: bool = False
    otel_exporter_otlp_endpoint: str | None = None
    otel_service_name: str = "ecommerce-agent"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
