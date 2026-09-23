"""FastAPI backend for Process Lab: the resource workbench and, in the same page as a tab, the
LangGraph simulation assistant.

Run from the AgentSimulator repo root (both evaluate_run.py and simulate.py resolve paths
relative to the process cwd, so this must be the working directory):

    python -m webapp.main

Binds to 127.0.0.1 by default (see .env / ORCHESTRATOR_HOST) — this agent has subprocess and
filesystem tool access, so it should not be exposed beyond localhost without adding auth first.
"""
from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))  # so `import evaluate_run` / `import source...` resolve

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .orchestrator import jobs
from .orchestrator.config import get_settings
from .orchestrator.llm import configure_langsmith
from .orchestrator.logging_utils import setup_logging
from .workbench.api import router as workbench_router
from .workbench.service import workbench
from .workbench.analyst import analyst

log = logging.getLogger(__name__)

MAX_MESSAGE_LENGTH = 2000


@asynccontextmanager
async def lifespan(app: FastAPI):
    # The scenario workbench is local and needs no LLM credentials.
    logging.basicConfig(level=logging.INFO)
    workbench.recover()
    analyst.recover(workbench)
    log.info("Orchestrator ready (cwd=%s)", Path.cwd())
    yield
    jobs.shutdown()


app = FastAPI(title="AgentSimulator Orchestrator", lifespan=lifespan)
app.include_router(workbench_router)


class MessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_MESSAGE_LENGTH)
    model_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{24}$")


class ConfirmIn(BaseModel):
    approved: bool


@app.post("/api/sessions")
def create_session():
    try:
        settings = get_settings()
        if not settings.deepseek_api_key.get_secret_value().strip():
            raise RuntimeError("Missing key")
        setup_logging()
        configure_langsmith()
    except RuntimeError:
        raise HTTPException(503, "Chat requires a DeepSeek API key in .env. The scenario workbench works without it.")
    return {"thread_id": jobs.create_session()}


@app.post("/api/sessions/{thread_id}/messages")
def post_message(thread_id: str, body: MessageIn):
    try:
        jobs.get_status(thread_id)
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
        jobs.submit_message(thread_id, body.text, body.model_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"status": "processing"}


@app.post("/api/sessions/{thread_id}/confirm")
def post_confirm(thread_id: str, body: ConfirmIn):
    try:
        session = jobs.get_status(thread_id)
    except KeyError:
        raise HTTPException(404, "Unknown session")
    if session.status != "confirmation_required":
        raise HTTPException(409, "No confirmation is pending for this session")
    try:
        jobs.submit_confirmation(thread_id, body.approved)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"status": "processing"}


@app.get("/api/sessions/{thread_id}/status")
def get_status(thread_id: str):
    try:
        session = jobs.get_status(thread_id)
    except KeyError:
        raise HTTPException(404, "Unknown session")
    return {
        "status": session.status,
        "interrupt": session.interrupt_payload,
        "messages": session.last_messages,
        "error": session.error,
        "revision": session.revision,
    }


static_dir = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/")
def index():
    return FileResponse(str(static_dir / "workbench.html"))


@app.get("/chat")
def chat_index():
    # The assistant is now a tab inside Process Lab (same page, same design), not a separate app.
    return RedirectResponse("/#assistant")


if __name__ == "__main__":
    import uvicorn

    import os
    from dotenv import load_dotenv
    load_dotenv(REPO_ROOT / ".env")
    uvicorn.run(app, host=os.getenv("ORCHESTRATOR_HOST", "127.0.0.1"), port=int(os.getenv("ORCHESTRATOR_PORT", "8000")))
