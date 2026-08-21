"""Builds the configured LLM provider, with a test override hook."""

from __future__ import annotations

from app.config import Settings
from app.config import settings as default_settings
from app.llm.base import LLMProvider
from app.llm.deterministic import DeterministicProvider
from app.llm.ollama import OllamaProvider
from app.llm.openai_compatible import OpenAICompatibleProvider

_override: LLMProvider | None = None


def set_llm_override(provider: LLMProvider | None) -> None:
    """Force a provider process-wide. Used by tests to inject a scripted model
    so the suite never needs a running Ollama."""
    global _override
    _override = provider


def build_llm_provider(settings: Settings | None = None) -> LLMProvider:
    if _override is not None:
        return _override

    cfg = settings or default_settings
    if cfg.llm_provider == "openai_compatible":
        return OpenAICompatibleProvider(
            base_url=cfg.openai_base_url,
            model=cfg.openai_model,
            api_key=cfg.openai_api_key,
            timeout=cfg.llm_timeout_seconds,
        )
    if cfg.llm_provider == "ollama":
        return OllamaProvider(
            base_url=cfg.ollama_base_url,
            model=cfg.ollama_model,
            timeout=cfg.llm_timeout_seconds,
        )
    if cfg.llm_provider == "test":
        # Deterministic CI provider: full orchestration without live inference.
        return DeterministicProvider()
    raise ValueError(f"Unknown LLM_PROVIDER: {cfg.llm_provider!r}")
