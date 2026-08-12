"""Runs the LangGraph agent off the HTTP request thread, and exposes a poll-friendly session
status so the FastAPI layer never blocks a request for the duration of a long simulation.

All graph invoke/resume calls are serialized behind one lock: SqliteSaver's single sqlite3
connection isn't safe for truly concurrent writers, and this is a local single-operator tool
where global serialization of LLM/graph steps is a fine, simple tradeoff (the heavy simulation
step is separately bounded by guardrails.ConcurrencyLimiter).
"""
from __future__ import annotations

import threading
import uuid
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
    _exit_stack.close()


def create_session() -> str:
    thread_id = uuid.uuid4().hex
    with _state_lock:
        _sessions[thread_id] = SessionState()
    return thread_id


def get_status(thread_id: str) -> SessionState:
    with _state_lock:
        session = _sessions.get(thread_id)
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
    graph = _get_graph()
    config = {"configurable": {"thread_id": thread_id}}
    try:
        before = graph.get_state(config)
        start_index = len(before.values.get("messages", [])) if before.values else 0

        with _graph_lock:
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
            session.error = str(exc)


def submit_message(thread_id: str, text: str) -> None:
    with _state_lock:
        _sessions[thread_id].status = "processing"
        _sessions[thread_id].error = None
    _executor.submit(_run_graph, thread_id, {"messages": [HumanMessage(content=text)]})


def submit_confirmation(thread_id: str, approved: bool) -> None:
    with _state_lock:
        _sessions[thread_id].status = "processing"
        _sessions[thread_id].error = None
    _executor.submit(_run_graph, thread_id, Command(resume=approved))
