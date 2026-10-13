"""Deterministic actions the graph can take. Never exposed to the LLM directly — the LLM only
proposes a ScenarioRequest (see graph.py); everything here runs after guardrails.py.

Compared with the previous research-engine version this is mostly *deletion*: there is no
subprocess, no argument-list construction, no retry/timeout loop of its own, because the
scenario runs on the engine in-process through the service's bounded single worker, which
already provides durable job state, crash recovery and isolated per-experiment artifacts.
"""
from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone

from ..core.config import get_settings
from ..core.storage import atomic_json
from ..core.errors import BusyError
from ..service.jobs import workbench
from .guardrails import ValidatedScenario, get_concurrency_limiter
from .schemas import RunManifest

log = logging.getLogger(__name__)

POLL_SECONDS = 1.0


class ExperimentBusyError(Exception):
    """Raised when the concurrency limiter or the job worker rejects a new run."""


def write_manifest(manifest: RunManifest) -> str:
    """Audit trail: one JSON per assistant-triggered run, never overwritten."""
    path = get_settings().runs_dir / "assistant" / f"{manifest.run_id}.json"
    atomic_json(path, manifest.model_dump(mode="json"))
    return str(path)


def run_scenario(validated: ValidatedScenario) -> RunManifest:
    """Submit the validated experiment and wait for the bounded worker to finish it.

    Concurrency-limited so the assistant cannot queue unbounded heavy jobs, and bounded by the
    engine's own 15-minute per-comparison budget plus a wall-clock ceiling here.
    """
    settings = get_settings()
    limiter = get_concurrency_limiter()
    run_id = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%S}_{uuid.uuid4().hex[:8]}"
    started_at = datetime.now(timezone.utc)

    def manifest(status: str, job_id: str | None = None, error: str | None = None) -> RunManifest:
        return RunManifest(
            run_id=run_id, dataset=validated.dataset, model_id=validated.model_id,
            scenario_name=validated.request.name,
            request=validated.request.model_dump(mode="json"),
            status=status, started_at=started_at,
            finished_at=datetime.now(timezone.utc), job_id=job_id, error=error,
        )

    if not limiter.try_acquire():
        raise ExperimentBusyError(
            f"Another experiment is already running (max_concurrent_jobs="
            f"{settings.max_concurrent_jobs}). Try again once it finishes."
        )
    try:
        try:
            job = workbench.submit("experiment", validated.request)
        except BusyError as exc:
            raise ExperimentBusyError(str(exc)) from exc
        except ValueError as exc:
            record = manifest("failed", error=str(exc))
            write_manifest(record)
            return record

        job_id = job["id"]
        log.info("assistant submitted experiment %s (run %s)", job_id, run_id)
        deadline = time.monotonic() + settings.experiment_timeout_seconds
        while True:
            current = workbench.job(job_id)
            if current["status"] in {"done", "error"}:
                break
            if time.monotonic() > deadline:
                record = manifest("timeout", job_id=job_id,
                                  error=f"Timed out after {settings.experiment_timeout_seconds}s")
                write_manifest(record)
                return record
            time.sleep(POLL_SECONDS)

        if current["status"] == "error":
            record = manifest("failed", job_id=job_id, error=current.get("error"))
        else:
            record = manifest("success", job_id=job_id)
        write_manifest(record)
        return record
    finally:
        limiter.release()


def experiment_result(job_id: str) -> dict:
    """The paired comparison and validation panel the service already computed."""
    return workbench.result(job_id)


def score_baseline(model: dict, repetitions: int = 2, seed: int = 42) -> dict:
    """The five published distance measures for this model's baseline (see scoring/metrics.py)."""
    from ..scoring.metrics import score_model

    return score_model(model, repetitions=repetitions, seed=seed)
