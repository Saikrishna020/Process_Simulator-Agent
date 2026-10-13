"""DeepSeek chat model + LangSmith tracing wiring.

LangSmith's tracer reads LANGSMITH_TRACING/LANGSMITH_API_KEY/LANGSMITH_PROJECT straight out of
os.environ, so configure_langsmith() must run before any LLM/graph call is made.
"""
from functools import lru_cache
import os

from .config import get_settings, require_llm_key


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
def build_llm(*, temperature: float = 0.0, max_tokens: int = 1024):
    """One ChatDeepSeek client per (temperature, max_tokens) combination, reused across every
    call. Constructing a new client per call rebuilt its underlying HTTP client and connection
    pool every time, paying a fresh TCP/TLS handshake per message instead of reusing a
    keep-alive connection.

    Imported lazily so that nothing in Process Lab's no-key path (engine, scenarios, the ten
    analyst presets) pulls in langchain at all.
    """
    from langchain_deepseek import ChatDeepSeek

    settings = require_llm_key()
    return ChatDeepSeek(
        model=settings.deepseek_model,
        api_key=settings.deepseek_api_key.get_secret_value(),
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=30,
        max_retries=1,
    )
