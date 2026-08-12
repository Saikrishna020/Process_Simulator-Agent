"""Deterministic actions the graph can take. Never exposed to the LLM directly — the LLM only
ever proposes a SimulationRequest (see graph.py); everything here runs after guardrails.py."""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .config import REPO_ROOT, get_settings
from .dataset_registry import available_datasets
from .guardrails import ValidatedRequest, get_concurrency_limiter
from .logging_utils import write_manifest
from .schemas import EvaluationSummary, RunManifest, SimulationRequest

log = logging.getLogger(__name__)


class SimulationBusyError(Exception):
    """Raised when the concurrency limiter rejects a new run."""


def list_available_datasets() -> list[dict]:
    return [
        {
            "name": e.name,
            "description": e.description,
            "case_id": e.case_id,
            "activity_name": e.activity_name,
            "resource": e.resource,
            "start_timestamp": e.start_timestamp,
            "end_timestamp": e.end_timestamp,
        }
        for e in available_datasets()
    ]


def _expected_output_dir(log_path: Path, request: SimulationRequest) -> Path:
    file_name = os.path.splitext(log_path.name)[0]
    if request.determine_automatically:
        extension = "main_results"
    elif request.central_orchestration:
        extension = "orchestrated"
    else:
        extension = "autonomous"
    return REPO_ROOT / "simulated_data" / file_name / extension


def _build_command(log_path: Path, request: SimulationRequest) -> list[str]:
    cmd = [
        sys.executable,
        "simulate.py",
        "--log_path", str(log_path),
        "--case_id", request.case_id,
        "--activity_name", request.activity_name,
        "--resource_name", request.resource,
        "--end_timestamp", request.end_timestamp,
        "--start_timestamp", request.start_timestamp,
        "--num_simulations", str(request.num_simulations),
    ]
    if request.determine_automatically:
        cmd.append("--determine_automatically")
    else:
        if request.central_orchestration:
            cmd.append("--central_orchestration")
        if request.extr_delays:
            cmd.append("--extr_delays")
    return cmd


def run_simulation(validated: ValidatedRequest) -> RunManifest:
    """Runs simulate.py as a subprocess (argument list, never shell=True), with a wall-clock
    timeout and a small bounded retry count. Concurrency-limited so at most N jobs run at once."""
    settings = get_settings()
    request = validated.request
    limiter = get_concurrency_limiter()

    if not limiter.try_acquire():
        raise SimulationBusyError(
            f"Another simulation is already running (max_concurrent_jobs="
            f"{settings.max_concurrent_jobs}). Try again once it finishes."
        )

    try:
        cmd = _build_command(validated.log_path, request)
        output_dir = _expected_output_dir(validated.log_path, request)
        run_id = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%S}_{uuid.uuid4().hex[:8]}"

        # Clear stale simulated_log_*.csv from a previous run with a different num_simulations —
        # otherwise leftover files make the post-run file count check unreliable.
        if output_dir.exists():
            for stale in output_dir.glob("simulated_log_*.csv"):
                stale.unlink()

        last_exc: Exception | None = None
        for attempt in range(1, settings.max_run_retries + 2):  # +1 initial + retries
            started_at = datetime.now(timezone.utc)
            log.info("run_simulation attempt %d: %s", attempt, " ".join(cmd))
            try:
                proc = subprocess.run(
                    cmd,
                    cwd=REPO_ROOT,
                    capture_output=True,
                    text=True,
                    timeout=settings.simulation_timeout_seconds,
                )
                finished_at = datetime.now(timezone.utc)
                sim_files = sorted(output_dir.glob("simulated_log_*.csv")) if output_dir.exists() else []
                success = proc.returncode == 0 and len(sim_files) == request.num_simulations

                manifest = RunManifest(
                    run_id=run_id,
                    dataset_name=request.dataset_name,
                    request=request,
                    status="success" if success else "failed",
                    attempt=attempt,
                    started_at=started_at,
                    finished_at=finished_at,
                    exit_code=proc.returncode,
                    output_dir=str(output_dir),
                    simulated_log_files=[f.name for f in sim_files],
                    stdout_tail=proc.stdout[-2000:],
                    stderr_tail=proc.stderr[-2000:],
                )
                write_manifest(manifest)

                if success:
                    return manifest
                last_exc = RuntimeError(
                    f"simulate.py exited {proc.returncode}, produced {len(sim_files)}/"
                    f"{request.num_simulations} logs. stderr tail: {proc.stderr[-500:]}"
                )
            except subprocess.TimeoutExpired:
                finished_at = datetime.now(timezone.utc)
                manifest = RunManifest(
                    run_id=run_id,
                    dataset_name=request.dataset_name,
                    request=request,
                    status="timeout",
                    attempt=attempt,
                    started_at=started_at,
                    finished_at=finished_at,
                    exit_code=None,
                    output_dir=str(output_dir),
                    stdout_tail="",
                    stderr_tail=f"Timed out after {settings.simulation_timeout_seconds}s",
                )
                write_manifest(manifest)
                return manifest  # timeouts are not retried

            log.warning("attempt %d failed: %s", attempt, last_exc)
            time.sleep(min(5 * attempt, 30))

        raise last_exc  # retries exhausted
    finally:
        limiter.release()


def evaluate_simulation(dataset_name: str, file_name: str, num_simulations: int) -> EvaluationSummary:
    """Reuses evaluate_run.evaluate() rather than reimplementing the distance metrics.

    `file_name` is the simulated_data/<file_name>/ subdirectory name (see _expected_output_dir),
    i.e. Path(manifest.output_dir).parent.name — not the raw dataset name.
    """
    import evaluate_run  # repo-root script; requires REPO_ROOT on sys.path and as cwd

    metrics = evaluate_run.evaluate(file_name, num_simulations)
    return EvaluationSummary(dataset_name=dataset_name, num_runs=num_simulations, metrics=metrics)
