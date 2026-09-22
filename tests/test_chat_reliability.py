"""Regressions for the failures observed in the live explorer/chat UI."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver

from webapp.orchestrator import jobs, graph
from webapp.workbench.model import atomic_json, discover


def test_windows_replace_retries_without_corrupting_visible_json(tmp_path, monkeypatch):
    path = tmp_path / "job.json"
    atomic_json(path, {"status": "queued"})
    original = Path.replace
    calls = []

    def locked_twice(source, destination):
        calls.append(source)
        if len(calls) < 3:
            assert json.loads(path.read_text()) == {"status": "queued"}
            raise PermissionError("reader temporarily holds destination")
        return original(source, destination)

    monkeypatch.setattr(Path, "replace", locked_twice)
    atomic_json(path, {"status": "done"})
    assert len(calls) == 3
    assert json.loads(path.read_text()) == {"status": "done"}
    assert not list(tmp_path.glob("*.tmp"))


def test_initialization_failure_finishes_processing_and_redacts_details(monkeypatch):
    session = jobs.create_session()
    monkeypatch.setattr(jobs, "_get_graph", lambda: (_ for _ in ()).throw(RuntimeError("secret-key")))
    jobs._run_graph(session, {"messages": []})
    status = jobs.get_status(session)
    assert status.status == "error"
    assert "secret-key" not in status.error
    status.status = "changed by caller"
    assert jobs.get_status(session).status == "error"


def test_duplicate_messages_and_unconfirmed_followups_are_rejected(monkeypatch):
    queued = []
    monkeypatch.setattr(jobs, "_executor", SimpleNamespace(submit=lambda *args: queued.append(args)))
    session = jobs.create_session()
    jobs.submit_message(session, "hello")
    with pytest.raises(ValueError, match="processing"):
        jobs.submit_message(session, "duplicate")
    jobs._sessions[session].status = "confirmation_required"
    with pytest.raises(ValueError, match="Confirm or cancel"):
        jobs.submit_message(session, "ignore pending plan")
    jobs.submit_confirmation(session, False)
    assert len(queued) == 2
    assert jobs.get_status(session).revision == 2
    assert jobs.get_status(session).last_messages == []


def test_model_questions_use_selected_history_without_starting_simulation(monkeypatch):
    from webapp.workbench import service
    model = dict(dataset="BPIC_2017_W", model_id="a" * 24,
                 explore={"cases": 31500, "resource_count": 149}, training_cases=24041,
                 resources=[dict(id="r0", name="User_1", event_count=5, case_count=3,
                                 activities={"Review": {"samples": [999999]}})], assumptions=[])
    monkeypatch.setattr(service.workbench, "get_model", lambda _: model)
    prompts = []

    class LLM:
        def bind_tools(self, _):
            return self

        def invoke(self, messages):
            prompts.append(messages[0].content)
            return AIMessage(content="The whole log contains 31,500 cases and 149 resources.")

    monkeypatch.setattr(graph, "build_llm", lambda: LLM())
    monkeypatch.setattr(graph.tools, "run_simulation", lambda *_: pytest.fail("Data question triggered simulation"))
    result = graph.build_graph(MemorySaver()).invoke(
        {"messages": [HumanMessage(content="How many cases?")], "model_id": "a" * 24},
        {"configurable": {"thread_id": "data-question"}})
    assert "__interrupt__" not in result
    assert '"cases": 31500' in prompts[0]
    assert '"training_cases": 24041' in prompts[0]
    assert "999999" not in prompts[0]
    assert "31,500" in result["messages"][-1].content


def test_single_task_cases_have_finite_explorer_handoff_statistics():
    import pandas as pd
    start = pd.date_range("2026-01-01", periods=20, tz="UTC")
    frame = pd.DataFrame(dict(case_id=[str(i) for i in range(20)], activity="Review", resource="A",
                              start=start, end=start + pd.Timedelta(hours=1)))
    model = discover(frame, "test", "a" * 24, "hash")
    assert model["explore"]["handover"]["same_resource_pct"] == 0
    json.dumps(model, allow_nan=False)


def test_independent_sessions_do_not_wait_for_another_graph_run(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    barrier = Barrier(2)

    class ConcurrentGraph:
        def get_state(self, config):
            return SimpleNamespace(values={})

        def invoke(self, data, config):
            barrier.wait(timeout=3)
            return {"messages": [AIMessage(content="Completed independently")]}

    monkeypatch.setattr(jobs, "_get_graph", ConcurrentGraph)
    sessions = [jobs.create_session(), jobs.create_session()]
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda session: jobs._run_graph(session, {}), sessions))
    assert all(jobs.get_status(session).status == "done" for session in sessions)
