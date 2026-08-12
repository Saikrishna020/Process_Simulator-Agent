"""DeepSeek chat model + LangSmith tracing wiring.

LangSmith's tracer reads LANGSMITH_TRACING/LANGSMITH_API_KEY/LANGSMITH_PROJECT straight out of
os.environ, so configure_langsmith() must run before any LLM/graph call is made.
"""
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


def build_llm(*, temperature: float = 0.0, max_tokens: int = 1024) -> ChatDeepSeek:
    settings = get_settings()
    return ChatDeepSeek(
        model=settings.deepseek_model,
        api_key=settings.deepseek_api_key.get_secret_value(),
        temperature=temperature,
        max_tokens=max_tokens,
    )
