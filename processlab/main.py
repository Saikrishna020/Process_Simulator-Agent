"""FastAPI backend for Process Lab: the scenario workbench and, as a tab in the same page,
the LangGraph simulation assistant.

Run from the repo root (the analyst and scoring resolve raw_data/ relative to the process cwd,
so that must be the working directory):

    python -m processlab.main

Binds to 127.0.0.1 by default (see .env / HOST) — the assistant has filesystem access, so it
should not be exposed beyond localhost without adding auth first.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .analysis.analyst import analyst
from .core.config import get_settings
from .core.logging_utils import setup_logging
from .service.api import router as workbench_router
from .service.jobs import workbench

log = logging.getLogger(__name__)

MAX_MESSAGE_LENGTH = 2000
STATIC_DIR = Path(__file__).resolve().parent / "web" / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Process Lab itself is local and needs no LLM credentials.
    setup_logging()
    workbench.recover()
    analyst.recover(workbench)
    try:
        assistant_state = "enabled" if get_settings().has_llm_key else "disabled: no API key"
    except Exception as exc:  # a startup log line must never stop the app from serving
        assistant_state = f"unknown ({type(exc).__name__})"
    log.info("Process Lab ready (cwd=%s, assistant=%s)", Path.cwd(), assistant_state)
    yield
    from .assistant import sessions
    sessions.shutdown()


app = FastAPI(title="Process Lab", lifespan=lifespan)
app.include_router(workbench_router)


class MessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_MESSAGE_LENGTH)
    model_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{24}$")


class ConfirmIn(BaseModel):
    approved: bool


def _sessions():
    """Imported lazily so a keyless install never pulls in langgraph/langchain."""
    from .assistant import sessions

    return sessions


@app.post("/api/sessions")
def create_session():
    settings = get_settings()
    if not settings.has_llm_key:
        raise HTTPException(
            503,
            "The assistant needs a DeepSeek API key in .env. Scenario builder, Data explorer "
            "and the suggested analyst questions all work without it.",
        )
    from .core.llm import configure_langsmith

    configure_langsmith()
    return {"thread_id": _sessions().create_session()}


@app.post("/api/sessions/{thread_id}/messages")
def post_message(thread_id: str, body: MessageIn):
    sessions = _sessions()
    try:
        sessions.get_status(thread_id)
    except KeyError:
        raise HTTPException(404, "Unknown session")
    if not body.text.strip():
        raise HTTPException(422, "Enter a message.")
    if body.model_id:
        try:
            workbench.get_model(body.model_id)
        except (FileNotFoundError, ValueError):
            raise HTTPException(422, "The selected model is unavailable. Learn the dataset again.")
    try:
        sessions.submit_message(thread_id, body.text, body.model_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"status": "processing"}


@app.post("/api/sessions/{thread_id}/confirm")
def post_confirm(thread_id: str, body: ConfirmIn):
    sessions = _sessions()
    try:
        session = sessions.get_status(thread_id)
    except KeyError:
        raise HTTPException(404, "Unknown session")
    if session.status != "confirmation_required":
        raise HTTPException(409, "No confirmation is pending for this session")
    try:
        sessions.submit_confirmation(thread_id, body.approved)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"status": "processing"}


@app.get("/api/sessions/{thread_id}/status")
def get_status(thread_id: str):
    try:
        session = _sessions().get_status(thread_id)
    except KeyError:
        raise HTTPException(404, "Unknown session")
    return {
        "status": session.status,
        "interrupt": session.interrupt_payload,
        "messages": session.last_messages,
        "error": session.error,
        "revision": session.revision,
    }


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def index():
    return FileResponse(str(STATIC_DIR / "workbench.html"))


@app.get("/chat")
def chat_index():
    # The assistant is a tab inside Process Lab (same page, same design), not a separate app.
    return RedirectResponse("/#assistant")


if __name__ == "__main__":
    import uvicorn
    from dotenv import load_dotenv

    from .core.config import REPO_ROOT

    load_dotenv(REPO_ROOT / ".env")
    settings = get_settings()
    uvicorn.run(app, host=settings.host, port=settings.port)
