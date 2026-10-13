"""Deterministic checks that stand between LLM output and any engine run.

Nothing the LLM proposes is trusted. Every resource and activity name is resolved against the
*selected model's* ground truth here, and the result is a strictly-validated ExperimentRequest —
the same type the web UI submits, with the same Pydantic caps. The engine's own
scenario_resources() is then used to reject structurally impossible scenarios (an unknown id, or
a removal that leaves an activity with nobody able to perform it) rather than reimplementing
those rules.
"""
from __future__ import annotations

import difflib
import threading
from dataclasses import dataclass
from pathlib import Path

from ..core.config import get_settings
from ..core.datasets import get_dataset
from ..engine.schemas import (
    ActivityChange, ExperimentRequest, ResourceChange, ResourceClone,
)
from ..engine.simulation import scenario_resources
from .schemas import ScenarioRequest


class GuardrailViolation(Exception):
    """Raised (and caught by the graph) when a proposal fails validation."""


@dataclass
class ValidatedScenario:
    request: ExperimentRequest        # resolved, strictly validated, ready for the job queue
    model_id: str
    dataset: str
    resolved_names: dict[str, str]    # human name -> internal id, for the confirmation card


def _resolve_within_raw_data(relative_path: str) -> Path:
    """Resolve `relative_path` against raw_data/ and refuse to leave it (no `..`, no absolutes).

    Kept even though the assistant no longer launches a subprocess: a dataset still becomes a
    filesystem read, and that read must stay inside raw_data/.
    """
    raw_data_dir = get_settings().raw_data_dir.resolve()
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


def _suggest(name: str, known: list[str]) -> str:
    close = difflib.get_close_matches(name, known, n=3, cutoff=0.6)
    if close:
        return " Did you mean: " + ", ".join(repr(c) for c in close) + "?"
    sample = ", ".join(repr(k) for k in known[:5])
    return f" Known values include: {sample}." if sample else ""


def _name_index(names: list[str]) -> dict[str, str]:
    """Case-insensitive, whitespace-tolerant lookup.

    BPI 2017 contains 'W_Shortened completion ' with a trailing space, so an exact-match-only
    index would make that activity unreachable by name.
    """
    index: dict[str, str] = {}
    for name in names:
        for key in {name, name.strip(), name.casefold(), name.strip().casefold()}:
            index.setdefault(key, name)
    return index


def _lookup(raw: str, index: dict[str, str], known: list[str], kind: str) -> str:
    for key in (raw, raw.strip(), raw.casefold(), raw.strip().casefold()):
        if key in index:
            return index[key]
    raise GuardrailViolation(f"Unknown {kind}: {raw!r}.{_suggest(raw, known)}")


def validate_scenario(model: dict, proposal: ScenarioRequest) -> ValidatedScenario:
    """Resolve names to ids and build a strictly-validated ExperimentRequest.

    Raises GuardrailViolation with a human-readable reason on any failure.
    """
    dataset = get_dataset(model["dataset"])
    if not dataset:
        raise GuardrailViolation(f"Dataset {model['dataset']!r} is not registered.")
    # The dataset still becomes a file read; keep it inside raw_data/.
    _resolve_within_raw_data(dataset.relative_log_path)

    resource_names = [r["name"] for r in model["resources"]]
    activity_names = [a["name"] for a in model["activities"]]
    by_resource_name = {r["name"]: r["id"] for r in model["resources"]}
    resource_index = _name_index(resource_names)
    activity_index = _name_index(activity_names)

    resolved: dict[str, str] = {}

    def resource_id(raw: str) -> str:
        actual = _lookup(raw, resource_index, resource_names, "resource")
        resolved[raw] = by_resource_name[actual]
        return by_resource_name[actual]

    def activity_name(raw: str) -> str:
        actual = _lookup(raw, activity_index, activity_names, "activity")
        resolved[raw] = actual
        return actual

    changes = [
        ResourceChange(
            resource_id=resource_id(change.resource_name),
            enabled=change.enabled,
            duration_multiplier=change.duration_multiplier,
        )
        for change in proposal.resource_changes
    ]
    clones = [
        ResourceClone(
            source_id=resource_id(clone.source_resource_name),
            name=clone.name or f"Copy of {clone.source_resource_name}",
            count=clone.count,
            duration_multiplier=clone.duration_multiplier,
        )
        for clone in proposal.clones
    ]
    activities = [
        ActivityChange(
            activity=activity_name(change.activity),
            duration_multiplier=change.duration_multiplier,
            delay_multiplier=change.delay_multiplier,
        )
        for change in proposal.activity_changes
    ]

    try:
        # ExperimentRequest's own validators enforce the caps: one change per resource/activity,
        # <=50 added resources, and every numeric range.
        request = ExperimentRequest(
            model_id=model["model_id"], name=proposal.name,
            horizon_days=proposal.horizon_days, repetitions=proposal.repetitions,
            seed=proposal.seed, sla_hours=proposal.sla_hours,
            demand_multiplier=proposal.demand_multiplier, routing=proposal.routing,
            resource_changes=changes, clones=clones, activity_changes=activities,
        )
    except Exception as exc:  # pydantic ValidationError -> a readable reason
        raise GuardrailViolation(str(exc)) from exc

    try:
        # The engine's own rule set: unknown ids, duplicate clone names, and removals that leave
        # an activity unperformable. Reused rather than duplicated here.
        scenario_resources(model, request)
    except ValueError as exc:
        raise GuardrailViolation(str(exc)) from exc

    return ValidatedScenario(
        request=request, model_id=model["model_id"], dataset=model["dataset"],
        resolved_names=resolved,
    )


class ConcurrencyLimiter:
    """Bounds how many assistant-triggered experiments can be in flight (self-DoS guard)."""

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
