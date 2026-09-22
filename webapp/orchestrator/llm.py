"""DeepSeek chat model + LangSmith tracing wiring.

LangSmith's tracer reads LANGSMITH_TRACING/LANGSMITH_API_KEY/LANGSMITH_PROJECT straight out of
os.environ, so configure_langsmith() must run before any LLM/graph call is made.
"""
from functools import lru_cache
import os

from langchain_deepseek import ChatDeepSeek

from .config import get_settings


def configure_langsmith() -> None:
    settings = get_settings()
    if settings.langsmith_tracing and settings.langsmith_api_key:
        os.environ["LANGSMITH_TRACING"] = "true"
        os.environ["LANGSMITH_API_KEY"] = settings.langsmith_api_key.get_secret_value()
        os.environ["LANGSMITH_PROJECT"] = settings.langsmith_project
    else:
        # Explicitly disabled — never leave tracing half-configured.
        os.environ["LANGSMITH_TRACING"] = "false"


@lru_cache
def build_llm(*, temperature: float = 0.0, max_tokens: int = 1024) -> ChatDeepSeek:
    """One ChatDeepSeek client per (temperature, max_tokens) combination, reused across every
    graph turn. Constructing a new client per call — the previous behaviour — rebuilt its
    underlying HTTP client and connection pool on every single message, paying a fresh TCP/TLS
    handshake for each turn instead of reusing a keep-alive connection. Chat models are designed
    to be built once and invoked many times; there is exactly one call site today (temperature=0,
    max_tokens=1024), so this cache holds a single instance for the process lifetime.
    """
    settings = get_settings()
    return ChatDeepSeek(
        model=settings.deepseek_model,
        api_key=settings.deepseek_api_key.get_secret_value(),
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=30,
        max_retries=1,
    )
