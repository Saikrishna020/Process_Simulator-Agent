"""The assistant's deterministic action layer, end to end, with no LLM involved.

The previous version of this file shelled out to the research engine and took ~2 minutes, which
is why CI had to skip it. The scenario now runs on Process Lab's own engine in-process, so the
whole end-to-end path — submit, bounded worker, paired comparison, audit manifest — is covered
in about a second and CI can run it on every push.
"""
from copy import deepcopy
from datetime import datetime, timezone
import threading

import pytest

from processlab.assistant.guardrails import ConcurrencyLimiter, validate_scenario
from processlab.assistant.schemas import ScenarioClone, ScenarioRequest
from processlab.assistant import tools
from processlab.core.storage import atomic_json, read_json
from processlab.engine.model import MODEL_VERSION
from processlab.service.jobs import Workbench

MONDAY = datetime(2026, 1, 5, tzinfo=timezone.utc).timestamp()
CALENDAR = [[9 * 3600, 17 * 3600] for _ in range(5)] + [None, None]


@pytest.fixture
def model():
    return dict(
        model_id="a" * 24, version=MODEL_VERSION, dataset="LoanApp", source_hash="abc",
        assumptions=["test model"], start_timestamp=MONDAY,
        historical_cycle_hours=dict(mean=1, median=1, p90=1),
        resources=[dict(id="r0", name="User_87", template_id="r0", calendar=deepcopy(CALENDAR),
                        event_count=4, case_count=4, observed_load_pct=10.0, handoffs=[],
                        activities={"Review": dict(count=4, samples=[3600])})],
        activities=[dict(name="Review", event_count=4)],
        templates=[[["Review", "r0", 0]]],
        arrival_days=[[[9 * 3600] * 4]] + [[[]] for _ in range(6)],
    )


@pytest.fixture
def store(tmp_path, model, monkeypatch):
    """A Workbench rooted in tmp_path, wired into the tools module."""
    workbench = Workbench(tmp_path)
    atomic_json(tmp_path / "models" / ("a" * 24 + ".json"), model)
    monkeypatch.setattr(tools, "workbench", workbench)
    yield workbench
    workbench.executor.shutdown()


def test_concurrency_limiter_bounds_parallel_jobs():
    limiter = ConcurrencyLimiter(1)
    assert limiter.try_acquire() is True
    assert limiter.try_acquire() is False, "a second slot must be refused, not queued"
    limiter.release()
    assert limiter.try_acquire() is True


def test_concurrency_limiter_is_thread_safe():
    limiter = ConcurrencyLimiter(2)
    granted = []

    def attempt():
        granted.append(limiter.try_acquire())

    threads = [threading.Thread(target=attempt) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(granted) == 2, "exactly two of eight racing callers may proceed"


def test_run_scenario_executes_and_writes_an_audit_manifest(store, model, monkeypatch,
                                                            tmp_path):
    manifests = tmp_path / "runs"
    monkeypatch.setattr(tools, "write_manifest",
                        lambda m: atomic_json(manifests / f"{m.run_id}.json",
                                              m.model_dump(mode="json")) or str(manifests))
    validated = validate_scenario(model, ScenarioRequest(
        name="Three more", horizon_days=7, repetitions=1,
        clones=[ScenarioClone(source_resource_name="User_87", count=3)]))

    manifest = tools.run_scenario(validated)

    assert manifest.status == "success", manifest.error
    assert manifest.dataset == "LoanApp"
    assert manifest.scenario_name == "Three more"
    assert manifest.job_id

    # The run really happened: the service wrote a paired comparison and the event logs.
    result = tools.experiment_result(manifest.job_id)
    assert result["request"]["name"] == "Three more"
    assert result["baseline_runs"] and result["scenario_runs"]
    assert "mean_cycle_hours" in result["comparison"]["metrics"]

    saved = read_json(manifests / f"{manifest.run_id}.json")
    assert saved["status"] == "success"
    assert saved["request"]["clones"][0]["source_id"] == "r0"


def test_an_unchanged_scenario_reproduces_the_baseline_through_the_assistant_path(store, model):
    """The determinism guarantee must survive the assistant's own code path, not just the UI's."""
    validated = validate_scenario(model, ScenarioRequest(
        name="No changes", horizon_days=7, repetitions=1))
    manifest = tools.run_scenario(validated)
    assert manifest.status == "success", manifest.error

    result = tools.experiment_result(manifest.job_id)
    for key, row in result["comparison"]["metrics"].items():
        assert row["delta"]["mean"] == 0, f"{key} moved without any change"


def test_a_busy_worker_is_surfaced_not_silently_queued(store, model, monkeypatch):
    from processlab.core.errors import BusyError

    def busy(*a, **k):
        raise BusyError("Another model or experiment is running. Wait for it to finish.")
    monkeypatch.setattr(store, "submit", busy)
    validated = validate_scenario(model, ScenarioRequest(name="x", horizon_days=7, repetitions=1))

    with pytest.raises(tools.ExperimentBusyError, match="Wait for it to finish"):
        tools.run_scenario(validated)


def test_an_engine_rejection_is_recorded_as_a_failed_manifest(store, model, monkeypatch):
    """A scenario the engine refuses mid-run must produce an auditable failure, not an exception."""
    written = {}
    monkeypatch.setattr(tools, "write_manifest", lambda m: written.update(m=m))

    def refuse(*a, **k):
        raise ValueError("This scenario exceeds 30,000 cases per repetition.")
    monkeypatch.setattr(store, "submit", refuse)
    validated = validate_scenario(model, ScenarioRequest(name="huge", horizon_days=7,
                                                         repetitions=1))

    manifest = tools.run_scenario(validated)
    assert manifest.status == "failed"
    assert "30,000 cases" in manifest.error
    assert written["m"].status == "failed"


def test_the_concurrency_slot_is_released_after_a_failure(store, model, monkeypatch):
    monkeypatch.setattr(tools, "write_manifest", lambda m: None)

    def refuse(*a, **k):
        raise ValueError("nope")
    monkeypatch.setattr(store, "submit", refuse)
    validated = validate_scenario(model, ScenarioRequest(name="x", horizon_days=7, repetitions=1))

    tools.run_scenario(validated)
    tools.run_scenario(validated)  # would raise ExperimentBusyError if the slot leaked

    limiter = tools.get_concurrency_limiter()
    assert limiter.try_acquire() is True
    limiter.release()
