"""Graph routing correctness with a stubbed LLM — no real DeepSeek calls, no cost."""
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from webapp.orchestrator import graph as graph_module

VALID_ARGS = dict(
    dataset_name="LoanApp",
    case_id="case_id",
    activity_name="activity",
    resource="resource",
    start_timestamp="start_time",
    end_timestamp="end_time",
    num_simulations=2,
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


def _build(monkeypatch, responses):
    monkeypatch.setattr(graph_module, "build_llm", lambda: FakeLLM(responses))
    return graph_module.build_graph(MemorySaver())


def test_clarifying_question_ends_turn_without_running_anything(monkeypatch):
    monkeypatch.setattr(
        graph_module.tools, "run_simulation", lambda *a, **k: (_ for _ in ()).throw(AssertionError)
    )
    g = _build(monkeypatch, [_tool_call_message("ClarificationNeeded", {"question": "Which dataset?"})])
    config = {"configurable": {"thread_id": "t-clarify"}}
    result = g.invoke({"messages": [HumanMessage(content="simulate something")]}, config)

    assert "__interrupt__" not in result
    assert any("Which dataset?" in m.content for m in result["messages"] if isinstance(m, AIMessage))


def test_invalid_plan_is_rejected_before_confirmation(monkeypatch):
    bad_args = {**VALID_ARGS, "case_id": "not_a_real_column"}
    g = _build(monkeypatch, [_tool_call_message("SimulationRequest", bad_args)])
    config = {"configurable": {"thread_id": "t-invalid"}}
    result = g.invoke({"messages": [HumanMessage(content="simulate LoanApp")]}, config)

    assert "__interrupt__" not in result
    assert result["validation_errors"]
    assert any("not_a_real_column" in m.content for m in result["messages"] if isinstance(m, AIMessage))


def test_valid_plan_pauses_for_confirmation_and_declining_skips_run(monkeypatch):
    ran = {"called": False}
    monkeypatch.setattr(graph_module.tools, "run_simulation", lambda *a, **k: ran.update(called=True))

    g = _build(monkeypatch, [_tool_call_message("SimulationRequest", VALID_ARGS)])
    config = {"configurable": {"thread_id": "t-decline"}}
    result = g.invoke({"messages": [HumanMessage(content="simulate LoanApp with 2 runs")]}, config)

    assert "__interrupt__" in result
    payload = result["__interrupt__"][0].value
    assert payload["dataset_name"] == "LoanApp"
    assert payload["num_simulations"] == 2

    result2 = g.invoke(Command(resume=False), config)
    assert "__interrupt__" not in result2
    assert ran["called"] is False
    assert any("won't run it" in m.content for m in result2["messages"] if isinstance(m, AIMessage))


def test_approving_confirmation_runs_and_summarizes(monkeypatch):
    from webapp.orchestrator.schemas import EvaluationSummary, RunManifest, SimulationRequest

    request = SimulationRequest(**VALID_ARGS)
    fake_manifest = RunManifest(
        run_id="test-run",
        dataset_name="LoanApp",
        request=request,
        status="success",
        attempt=1,
        started_at="2026-01-01T00:00:00Z",
        finished_at="2026-01-01T00:00:05Z",
        exit_code=0,
        output_dir="simulated_data/LoanApp.csv/main_results",
        simulated_log_files=["simulated_log_0.csv", "simulated_log_1.csv"],
    )
    fake_summary = EvaluationSummary(
        dataset_name="LoanApp", num_runs=2,
        metrics={"NGD": [0.1, 0.2], "AEDD": [1.0, 2.0], "CEDD": [0.1, 0.1], "REDD": [1.0, 1.0], "CTDD": [1.0, 1.0]},
    )
    monkeypatch.setattr(graph_module.tools, "run_simulation", lambda *a, **k: fake_manifest)
    monkeypatch.setattr(graph_module.tools, "evaluate_simulation", lambda *a, **k: fake_summary)

    g = _build(monkeypatch, [_tool_call_message("SimulationRequest", VALID_ARGS)])
    config = {"configurable": {"thread_id": "t-approve"}}
    g.invoke({"messages": [HumanMessage(content="simulate LoanApp with 2 runs")]}, config)
    result = g.invoke(Command(resume=True), config)

    assert "__interrupt__" not in result
    final_text = [m.content for m in result["messages"] if isinstance(m, AIMessage) and m.content][-1]
    assert "LoanApp" in final_text
    assert "NGD" in final_text
