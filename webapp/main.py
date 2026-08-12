"""FastAPI chat UI for the simulation agent.

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
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .orchestrator import jobs
from .orchestrator.config import get_settings
from .orchestrator.llm import configure_langsmith
from .orchestrator.logging_utils import setup_logging

log = logging.getLogger(__name__)

MAX_MESSAGE_LENGTH = 2000


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    get_settings()  # fail fast if .env is missing/invalid
    configure_langsmith()
    log.info("Orchestrator ready (cwd=%s)", Path.cwd())
    yield
    jobs.shutdown()


app = FastAPI(title="AgentSimulator Orchestrator", lifespan=lifespan)


class MessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_MESSAGE_LENGTH)


class ConfirmIn(BaseModel):
    approved: bool


@app.post("/api/sessions")
def create_session():
    return {"thread_id": jobs.create_session()}


@app.post("/api/sessions/{thread_id}/messages")
def post_message(thread_id: str, body: MessageIn):
    try:
        jobs.get_status(thread_id)
    except KeyError:
        raise HTTPException(404, "Unknown session")
    jobs.submit_message(thread_id, body.text)
    return {"status": "processing"}


@app.post("/api/sessions/{thread_id}/confirm")
def post_confirm(thread_id: str, body: ConfirmIn):
    try:
        session = jobs.get_status(thread_id)
    except KeyError:
        raise HTTPException(404, "Unknown session")
    if session.status != "confirmation_required":
        raise HTTPException(409, "No confirmation is pending for this session")
    jobs.submit_confirmation(thread_id, body.approved)
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
    }


static_dir = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/")
def index():
    return FileResponse(str(static_dir / "index.html"))


if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    uvicorn.run(app, host=settings.orchestrator_host, port=settings.orchestrator_port)
