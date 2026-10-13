"""Graph routing correctness with a stubbed LLM — no real DeepSeek calls, no cost.

The assistant now proposes what-if *scenarios* that run on Process Lab's own engine, so these
tests assert the name-resolution boundary too: the LLM names people, code resolves them.
"""
from copy import deepcopy
from datetime import datetime, timezone

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command
import pytest

from processlab.assistant import graph as graph_module
from processlab.engine.model import MODEL_VERSION

MONDAY = datetime(2026, 1, 5, tzinfo=timezone.utc).timestamp()
CALENDAR = [[9 * 3600, 17 * 3600] for _ in range(5)] + [None, None]
MODEL_ID = "a" * 24


@pytest.fixture
def model():
    return dict(
        model_id=MODEL_ID, version=MODEL_VERSION, dataset="LoanApp", source_hash="abc",
        assumptions=[], start_timestamp=MONDAY,
        historical_cycle_hours=dict(mean=1, median=1, p90=1),
        explore=dict(cases=10, events=10),
        training_cases=10, test_cases=2, excluded_boundary_cases=0,
        resources=[
            dict(id="r0", name="User_87", template_id="r0", calendar=deepcopy(CALENDAR),
                 event_count=9, case_count=9, observed_load_pct=20.0, handoffs=[],
                 activities={"Review": dict(count=9, samples=[3600])}),
            dict(id="r1", name="User_5", template_id="r1", calendar=deepcopy(CALENDAR),
                 event_count=1, case_count=1, observed_load_pct=5.0, handoffs=[],
                 activities={"Review": dict(count=1, samples=[1800])}),
        ],
        activities=[dict(name="Review", event_count=10)],
        templates=[[["Review", "r0", 0]]],
        arrival_days=[[[9 * 3600] * 4]] + [[[]] for _ in range(6)],
    )


class FakeLLM:
    """Mimics ChatDeepSeek.bind_tools(...).invoke(...) with a scripted sequence of responses."""

    def __init__(self, responses):
        self._responses = list(responses)

    def bind_tools(self, _tools):
        return self

    def invoke(self, _messages):
        return self._responses.pop(0)


def _tool_call_message(name, args, call_id="call1"):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])


def _build(monkeypatch, responses, model=None):
    monkeypatch.setattr(graph_module, "build_llm", lambda: FakeLLM(responses))
    if model is not None:
        from processlab.service import jobs

        monkeypatch.setattr(jobs.workbench, "get_model", lambda _id: model)
    return graph_module.build_graph(MemorySaver())


def _start(graph, text, thread, model_id=MODEL_ID):
    config = {"configurable": {"thread_id": thread}}
    return graph.invoke({"messages": [HumanMessage(content=text)], "model_id": model_id}, config), config


def _ai_text(result):
    return [m.content for m in result["messages"] if isinstance(m, AIMessage) and m.content]


def _never_runs(monkeypatch):
    def explode(*a, **k):
        raise AssertionError("run_scenario must not be called")
    monkeypatch.setattr(graph_module.tools, "run_scenario", explode)


def test_clarifying_question_ends_turn_without_running_anything(monkeypatch, model):
    _never_runs(monkeypatch)
    g = _build(monkeypatch,
               [_tool_call_message("ClarificationNeeded", {"question": "Which resource?"})], model)
    result, _ = _start(g, "change some staffing", "t-clarify")

    assert "__interrupt__" not in result
    assert any("Which resource?" in text for text in _ai_text(result))


def test_a_plain_data_answer_is_not_turned_into_a_run(monkeypatch, model):
    _never_runs(monkeypatch)
    g = _build(monkeypatch, [AIMessage(content="User_87 handled 9 of 10 recorded tasks.")], model)
    result, _ = _start(g, "who does the most work?", "t-data")

    assert "__interrupt__" not in result
    assert any("User_87" in text for text in _ai_text(result))


def test_unknown_resource_name_is_rejected_before_confirmation(monkeypatch, model):
    _never_runs(monkeypatch)
    g = _build(monkeypatch, [_tool_call_message("ScenarioRequest", {
        "name": "bad", "clones": [{"source_resource_name": "User_999", "count": 1}]})], model)
    result, _ = _start(g, "add a copy of User_999", "t-unknown")

    assert "__interrupt__" not in result
    assert result["validation_errors"]
    assert any("User_999" in text for text in _ai_text(result))


def test_out_of_range_demand_is_rejected_before_confirmation(monkeypatch, model):
    _never_runs(monkeypatch)
    # 99x demand is outside ScenarioRequest's own bounds, so this never even reaches the engine.
    g = _build(monkeypatch, [_tool_call_message("ScenarioRequest", {
        "name": "flood", "demand_multiplier": 99})], model)
    with pytest.raises(Exception):
        _start(g, "simulate 99 times the demand", "t-demand")


def test_removing_the_only_qualified_resource_is_rejected(monkeypatch, model):
    """The engine's own rule, reused rather than reimplemented in the guardrail."""
    _never_runs(monkeypatch)
    g = _build(monkeypatch, [_tool_call_message("ScenarioRequest", {
        "name": "strip", "resource_changes": [
            {"resource_name": "User_87", "enabled": False},
            {"resource_name": "User_5", "enabled": False}]})], model)
    result, _ = _start(g, "remove everyone", "t-strip")

    assert "__interrupt__" not in result
    assert result["validation_errors"]
    assert any("Review" in text for text in _ai_text(result))


def test_scenario_without_a_selected_model_is_refused(monkeypatch, model):
    _never_runs(monkeypatch)
    g = _build(monkeypatch, [_tool_call_message("ScenarioRequest", {"name": "x"})], model)
    config = {"configurable": {"thread_id": "t-nomodel"}}
    result = g.invoke({"messages": [HumanMessage(content="run it")], "model_id": None}, config)

    assert "__interrupt__" not in result
    assert result["validation_errors"]
    assert any("Learn resource profiles" in text for text in _ai_text(result))


def test_valid_plan_pauses_for_confirmation_and_declining_skips_run(monkeypatch, model):
    ran = {"called": False}
    monkeypatch.setattr(graph_module.tools, "run_scenario",
                        lambda *a, **k: ran.update(called=True))
    g = _build(monkeypatch, [_tool_call_message("ScenarioRequest", {
        "name": "Three more of User_87",
        "clones": [{"source_resource_name": "User_87", "count": 3}],
        "activity_changes": [{"activity": "Review", "delay_multiplier": 0.5}]})], model)
    result, config = _start(g, "add 3 copies of User_87 and halve Review delay", "t-decline")

    assert "__interrupt__" in result
    payload = result["__interrupt__"][0].value
    assert payload["type"] == "confirmation_required"
    assert payload["dataset"] == "LoanApp"
    assert any("3 copy(ies) of User_87" in change for change in payload["changes"])
    assert any("residual delay x0.5" in change for change in payload["changes"])

    declined = g.invoke(Command(resume=False), config)
    assert "__interrupt__" not in declined
    assert ran["called"] is False
    assert any("won't run it" in text for text in _ai_text(declined))


def test_the_llm_names_people_and_code_resolves_them_to_ids(monkeypatch, model):
    """Case-insensitive names resolve; the LLM never supplies an internal id."""
    monkeypatch.setattr(graph_module.tools, "run_scenario", lambda *a, **k: None)
    g = _build(monkeypatch, [_tool_call_message("ScenarioRequest", {
        "name": "lowercase", "resource_changes": [
            {"resource_name": "user_87", "duration_multiplier": 0.8}]})], model)
    result, _ = _start(g, "make user_87 20% faster", "t-resolve")

    assert "__interrupt__" in result
    assert result["resolved"] == {"user_87": "r0"}


def test_approving_confirmation_runs_and_reports_the_paired_comparison(monkeypatch, model):
    from processlab.assistant.schemas import RunManifest

    manifest = RunManifest(
        run_id="test-run", dataset="LoanApp", model_id=MODEL_ID,
        scenario_name="Three more of User_87",
        request={"repetitions": 2, "horizon_days": 30},
        status="success", started_at="2026-01-01T00:00:00Z",
        finished_at="2026-01-01T00:00:05Z", job_id="b" * 32,
    )
    comparison = {
        "comparison": {"metrics": {
            "mean_cycle_hours": {"baseline": {"mean": 337.0}, "scenario": {"mean": 300.0},
                                 "change_pct": -11.0},
            "mean_wait_hours": {"baseline": {"mean": 61.5}, "scenario": {"mean": 2.4},
                                "change_pct": -96.1},
        }},
        "validation": {"cases": 3937, "cycle": [
            {"label": "Mean case duration (h)", "real": 300.4, "simulated": 337.0,
             "difference_pct": 12.2}]},
    }
    monkeypatch.setattr(graph_module.tools, "run_scenario", lambda *a, **k: manifest)
    monkeypatch.setattr(graph_module.tools, "experiment_result", lambda *a, **k: comparison)

    g = _build(monkeypatch, [_tool_call_message("ScenarioRequest", {
        "name": "Three more of User_87",
        "clones": [{"source_resource_name": "User_87", "count": 3}]})], model)
    _, config = _start(g, "add 3 copies of User_87", "t-approve")
    result = g.invoke(Command(resume=True), config)

    assert "__interrupt__" not in result
    final = _ai_text(result)[-1]
    assert "Three more of User_87" in final
    assert "Mean case duration" in final
    assert "-96.1%" in final                 # the paired comparison is reported
    assert "+12.2%" in final                 # so is the baseline's honest fidelity gap
    assert "3937" in final or "3,937" in final


def test_a_busy_worker_is_reported_not_retried_forever(monkeypatch, model):
    from processlab.assistant.tools import ExperimentBusyError

    def busy(*a, **k):
        raise ExperimentBusyError("Another experiment is already running.")
    monkeypatch.setattr(graph_module.tools, "run_scenario", busy)
    g = _build(monkeypatch, [_tool_call_message("ScenarioRequest", {
        "name": "queued", "demand_multiplier": 1.5})], model)
    _, config = _start(g, "simulate 50% more demand", "t-busy")
    result = g.invoke(Command(resume=True), config)

    assert "__interrupt__" not in result
    assert any("already running" in text for text in _ai_text(result))
