"""Runs the LangGraph agent off the HTTP request thread, and exposes a poll-friendly session
status so the FastAPI layer never blocks a request for the duration of a long simulation.

Graph initialization is serialized. SqliteSaver locks its own database operations;
independent sessions can therefore make progress while another awaits an LLM or
simulation. Submission guards allow only one operation per session. Heavy research
simulations are separately bounded by guardrails.ConcurrencyLimiter.
"""
from __future__ import annotations

import threading
import uuid
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from .config import get_settings
from .graph import build_graph
from .llm import configure_langsmith

_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="agent-graph")
_graph_lock = threading.Lock()
_state_lock = threading.Lock()

_exit_stack = ExitStack()
_graph = None


@dataclass
class SessionState:
    status: str = "idle"  # idle | processing | confirmation_required | done | error
    interrupt_payload: dict[str, Any] | None = None
    last_messages: list[dict[str, str]] = field(default_factory=list)
    error: str | None = None
    revision: int = 0


_sessions: dict[str, SessionState] = {}


def _get_graph():
    global _graph
    if _graph is None:
        configure_langsmith()
        settings = get_settings()
        db_path = settings.runs_dir / "checkpoints.sqlite"
        checkpointer = _exit_stack.enter_context(SqliteSaver.from_conn_string(str(db_path)))
        _graph = build_graph(checkpointer)
    return _graph


def shutdown() -> None:
    global _graph, _executor
    # Finish workers before closing the checkpointer they still use.
    _executor.shutdown(wait=True)
    with _graph_lock:
        _exit_stack.close()
        _graph = None
    _executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="agent-graph")


def create_session() -> str:
    thread_id = uuid.uuid4().hex
    with _state_lock:
        _sessions[thread_id] = SessionState()
    return thread_id


def get_status(thread_id: str) -> SessionState:
    with _state_lock:
        session = deepcopy(_sessions.get(thread_id))
    if session is None:
        raise KeyError(thread_id)
    return session


def _extract_visible_messages(all_messages: list, start_index: int) -> list[dict[str, str]]:
    visible = []
    for m in all_messages[start_index:]:
        if isinstance(m, AIMessage) and m.content and not getattr(m, "tool_calls", None):
            visible.append({"role": "assistant", "content": m.content})
    return visible


def _run_graph(thread_id: str, invoke_input) -> None:
    config = {
        "configurable": {"thread_id": thread_id},
        # Purely for LangSmith: without these every trace shows up as an anonymous "LangGraph"
        # run, indistinguishable from any other graph in the project. `tags` stays a small fixed
        # vocabulary (LangSmith filters by tag, so a per-session value here would create one new
        # tag per conversation); `thread_id` already groups a session's turns in the LangSmith UI
        # via `configurable.thread_id` above — metadata just makes that visible in the run itself.
        "run_name": "simulation-assistant-turn",
        "tags": ["simulation-assistant"],
        "metadata": {"thread_id": thread_id},
    }
    try:
        with _graph_lock:
            # Initialization must be inside try or failures leave processing forever.
            graph = _get_graph()
        before = graph.get_state(config)
        start_index = len(before.values.get("messages", [])) if before.values else 0
        result = graph.invoke(invoke_input, config)

        with _state_lock:
            session = _sessions[thread_id]
            interrupts = result.get("__interrupt__")
            if interrupts:
                session.status = "confirmation_required"
                session.interrupt_payload = interrupts[0].value
                session.last_messages = _extract_visible_messages(
                    result.get("messages", []), start_index
                )
            else:
                session.status = "done"
                session.interrupt_payload = None
                session.last_messages = _extract_visible_messages(
                    result.get("messages", []), start_index
                )
    except Exception as exc:  # last-resort guard so the UI never hangs on "processing" forever
        with _state_lock:
            session = _sessions[thread_id]
            session.status = "error"
            session.error = _friendly_error(exc)


def _friendly_error(exc: Exception) -> str:
    # Provider exceptions can include request headers or configuration values.
    name = type(exc).__name__
    if "Authentication" in name:
        return "DeepSeek rejected the API key. Check DEEPSEEK_API_KEY in .env and restart the server."
    if "Connection" in name or "Timeout" in name:
        return "Could not reach DeepSeek within the time limit. Check the server's network/proxy configuration, then retry."
    if "RateLimit" in name:
        return "DeepSeek's rate or account limit was reached. Check the account and retry later."
    return f"The assistant could not complete this request ({name}). Please retry or restart the server."


def _submit(thread_id: str, invoke_input, *, confirmation=False):
    with _state_lock:
        session = _sessions[thread_id]
        if session.status == "processing":
            raise ValueError("This session is still processing a request.")
        if confirmation != (session.status == "confirmation_required"):
            raise ValueError("Confirm or cancel the pending plan first." if not confirmation else "No confirmation is pending.")
        session.status = "processing"
        session.error = None
        session.last_messages = []
        session.interrupt_payload = None
        session.revision += 1
    try:
        _executor.submit(_run_graph, thread_id, invoke_input)
    except Exception as exc:
        with _state_lock:
            session.status = "error"
            session.error = _friendly_error(exc)
        raise


def submit_message(thread_id: str, text: str, model_id: str | None = None) -> None:
    _submit(thread_id, {"messages": [HumanMessage(content=text)], "model_id": model_id,
                       "manifest": None, "evaluation": None, "retry_count": 0})


def submit_confirmation(thread_id: str, approved: bool) -> None:
    _submit(thread_id, Command(resume=approved), confirmation=True)
