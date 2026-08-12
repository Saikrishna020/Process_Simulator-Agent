"""Deterministic checks that stand between LLM output and any filesystem/subprocess action.

Nothing the LLM proposes is trusted: every field of a SimulationRequest is re-derived or
re-checked against ground truth here before run_simulation ever touches a subprocess.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .config import get_settings
from .dataset_registry import get_dataset
from .schemas import SimulationRequest


class GuardrailViolation(Exception):
    """Raised (and caught by the graph) when a request fails validation."""


@dataclass
class ValidatedRequest:
    request: SimulationRequest
    log_path: Path  # absolute, verified to be inside raw_data/


def _resolve_within_raw_data(relative_path: str) -> Path:
    """Resolve `relative_path` against raw_data/ and refuse to leave it (no `..`, no absolute paths)."""
    settings = get_settings()
    raw_data_dir = settings.raw_data_dir.resolve()

    candidate = (raw_data_dir / relative_path).resolve()
    try:
        candidate.relative_to(raw_data_dir)
    except ValueError:
        raise GuardrailViolation(
            f"'{relative_path}' resolves outside raw_data/ — refusing to read it."
        )
    if not candidate.exists():
        raise GuardrailViolation(f"No such file under raw_data/: {relative_path}")
    if not candidate.is_file():
        raise GuardrailViolation(f"Not a file: {relative_path}")
    return candidate


def _read_header(path: Path) -> list[str]:
    try:
        df = pd.read_csv(path, nrows=0)
    except Exception as exc:
        raise GuardrailViolation(f"Could not read '{path.name}' as CSV: {exc}")
    return list(df.columns)


def validate_request(request: SimulationRequest) -> ValidatedRequest:
    """Raises GuardrailViolation with a human-readable reason on any failure."""
    settings = get_settings()
    errors: list[str] = []

    registered = get_dataset(request.dataset_name)
    relative_path = registered.relative_log_path if registered else request.dataset_name

    log_path = _resolve_within_raw_data(relative_path)

    header = _read_header(log_path)
    for field_name in ("case_id", "activity_name", "resource", "start_timestamp", "end_timestamp"):
        col = getattr(request, field_name)
        if col not in header:
            errors.append(
                f"Column '{col}' (for {field_name}) not found in {log_path.name}. "
                f"Actual columns: {header}"
            )

    if not (1 <= request.num_simulations <= settings.max_num_simulations):
        errors.append(
            f"num_simulations={request.num_simulations} is outside the allowed range "
            f"[1, {settings.max_num_simulations}]"
        )

    if errors:
        raise GuardrailViolation("; ".join(errors))

    return ValidatedRequest(request=request, log_path=log_path)


class ConcurrencyLimiter:
    """Bounds how many simulate.py subprocesses can run at once (self-DoS guard)."""

    def __init__(self, max_concurrent: int):
        self._semaphore = threading.Semaphore(max_concurrent)

    def try_acquire(self) -> bool:
        return self._semaphore.acquire(blocking=False)

    def release(self) -> None:
        self._semaphore.release()


_limiter: ConcurrencyLimiter | None = None


def get_concurrency_limiter() -> ConcurrencyLimiter:
    global _limiter
    if _limiter is None:
        _limiter = ConcurrencyLimiter(get_settings().max_concurrent_jobs)
    return _limiter
