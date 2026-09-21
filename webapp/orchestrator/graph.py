"""The LangGraph flow: understand -> validate -> confirm (human-in-the-loop) -> run -> evaluate
-> summarize. Every LLM output is re-validated deterministically before it can touch a file or
subprocess; see guardrails.py.
"""
from __future__ import annotations

import logging

from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from . import tools
from .config import get_settings
from .dataset_registry import available_datasets
from .guardrails import GuardrailViolation, validate_request
from .llm import build_llm
from .schemas import ClarificationNeeded, EvaluationSummary, RunManifest, SimulationRequest
from .state import AgentState
from .tools import SimulationBusyError

log = logging.getLogger(__name__)

METRIC_EXPLANATIONS = {
    "NGD": "N-Gram Distance - how different the sequences/patterns of activities are",
    "AEDD": "Absolute Event Distribution Distance - how different the timing of events is on an absolute clock",
    "CEDD": "Circadian Event Distribution Distance - how different activity is across hours of the day/week",
    "REDD": "Relative Event Distribution Distance - how different event timing is relative to each case's start",
    "CTDD": "Cycle Time Distribution Distance - how different overall case durations are",
}


def _system_prompt() -> str:
    datasets = available_datasets()
    lines = [
        "You are the planning stage of a business-process simulation agent.",
        "Your ONLY job is to turn the user's request into a precise simulation plan by calling "
        "the `SimulationRequest` tool, or, if the request is unclear or the dataset isn't listed "
        "below, call `ask_clarifying_question` instead.",
        "Never invent a dataset or column name that isn't listed below — if the user names "
        "something not listed, ask them to pick from this list or ask for its exact column names.",
        "",
        "Available datasets:",
    ]
    for d in datasets:
        lines.append(
            f"- {d.name}: {d.description} "
            f"(case_id={d.case_id}, activity={d.activity_name}, resource={d.resource}, "
            f"start={d.start_timestamp}, end={d.end_timestamp})"
        )
    if not datasets:
        lines.append("- (none currently available on disk)")
    lines.append(
        "\nDefault num_simulations to 10 unless the user asks for fewer/more. Default "
        "determine_automatically to true unless the user explicitly wants a specific "
        "central_orchestration/extr_delays configuration."
    )
    return "\n".join(lines)


def understand_request(state: AgentState) -> dict:
    llm = build_llm()
    llm_with_tools = llm.bind_tools([SimulationRequest, ClarificationNeeded])
    messages = [SystemMessage(content=_system_prompt()), *state["messages"]]
    response = llm_with_tools.invoke(messages)

    if not response.tool_calls:
        # Model answered directly instead of calling a tool — treat it as a clarification.
        return {"messages": [response], "route": "clarify"}

    call = response.tool_calls[0]
    if call["name"] == "SimulationRequest":
        request = SimulationRequest(**call["args"])
        ack = ToolMessage(content="Plan captured.", tool_call_id=call["id"])
        return {"messages": [response, ack], "request": request.model_dump(), "route": "propose"}

    question = ClarificationNeeded(**call["args"]).question
    ack = ToolMessage(content=question, tool_call_id=call["id"])
    return {"messages": [response, ack, AIMessage(content=question)], "route": "clarify"}


def route_after_understand(state: AgentState) -> str:
    return "validate" if state.get("route") == "propose" else "end"


def validate(state: AgentState) -> dict:
    settings = get_settings()
    request = SimulationRequest(**state["request"])
    try:
        validate_request(request)
        return {"validation_errors": []}
    except GuardrailViolation as exc:
        retry_count = state.get("retry_count", 0) + 1
        if retry_count >= settings.max_clarification_turns:
            msg = (
                f"I still can't build a valid plan after {retry_count} attempts "
                f"({exc}). Please double-check the dataset/column names and try again."
            )
        else:
            msg = f"That plan isn't valid: {exc}. Could you clarify?"
        return {
            "validation_errors": [str(exc)],
            "retry_count": retry_count,
            "messages": [AIMessage(content=msg)],
        }


def route_after_validate(state: AgentState) -> str:
    return "confirm" if not state.get("validation_errors") else "end"


def confirm_run(state: AgentState) -> dict:
    request = SimulationRequest(**state["request"])
    approved = interrupt(
        {
            "type": "confirmation_required",
            "dataset_name": request.dataset_name,
            "num_simulations": request.num_simulations,
            "determine_automatically": request.determine_automatically,
            "central_orchestration": request.central_orchestration,
            "extr_delays": request.extr_delays,
            "columns": {
                "case_id": request.case_id,
                "activity_name": request.activity_name,
                "resource": request.resource,
                "start_timestamp": request.start_timestamp,
                "end_timestamp": request.end_timestamp,
            },
        }
    )
    if not approved:
        return {"confirmed": False, "messages": [AIMessage(content="Okay, I won't run it.")]}
    return {"confirmed": True}


def route_after_confirm(state: AgentState) -> str:
    return "run" if state.get("confirmed") else "end"


def run_simulation_node(state: AgentState) -> dict:
    request = SimulationRequest(**state["request"])
    try:
        validated = validate_request(request)  # re-check right before executing
    except GuardrailViolation as exc:
        return {"manifest": None, "messages": [AIMessage(content=f"Plan became invalid: {exc}")]}
    try:
        manifest = tools.run_simulation(validated)
    except SimulationBusyError as exc:
        return {"manifest": None, "messages": [AIMessage(content=str(exc))]}
    return {"manifest": manifest.model_dump(mode="json")}


def route_after_run(state: AgentState) -> str:
    manifest = state.get("manifest")
    if manifest is None:
        return "end"
    return "evaluate" if manifest["status"] == "success" else "summarize"


def evaluate_results(state: AgentState) -> dict:
    manifest = RunManifest(**state["manifest"])
    from pathlib import Path

    file_name = Path(manifest.output_dir).parent.name
    try:
        summary = tools.evaluate_simulation(
            manifest.dataset_name, file_name, len(manifest.simulated_log_files), output_dir=manifest.output_dir
        )
        return {"evaluation": summary.model_dump()}
    except Exception as exc:  # evaluation is best-effort — the run itself already succeeded
        log.warning("evaluation failed: %s", exc)
        return {"evaluation": {"error": str(exc)}}


def summarize(state: AgentState) -> dict:
    manifest = state.get("manifest")
    evaluation = state.get("evaluation")

    if manifest is None:
        return {"messages": [AIMessage(content="The run did not complete; see the message above.")]}

    if manifest["status"] != "success":
        text = (
            f"The simulation for {manifest['dataset_name']} did not finish successfully "
            f"(status: {manifest['status']}, exit_code: {manifest.get('exit_code')}). "
            f"Details: {manifest.get('stderr_tail') or 'no error output captured'}"
        )
        return {"messages": [AIMessage(content=text)]}

    lines = [
        f"Simulation of **{manifest['dataset_name']}** finished - "
        f"{len(manifest['simulated_log_files'])} synthetic logs written to `{manifest['output_dir']}`.",
    ]
    if evaluation and "error" not in evaluation:
        means = EvaluationSummary(**evaluation).means()
        lines.append("\nEvaluation vs. the held-out test split (lower = more realistic):")
        for metric, value in means.items():
            explanation = METRIC_EXPLANATIONS.get(metric, "")
            lines.append(f"- **{metric}** = {value:.3f} - {explanation}")
    elif evaluation:
        lines.append(f"\n(Evaluation could not be computed: {evaluation['error']})")

    return {"messages": [AIMessage(content="\n".join(lines))]}


def build_graph(checkpointer: BaseCheckpointSaver):
    graph = StateGraph(AgentState)
    graph.add_node("understand_request", understand_request)
    graph.add_node("validate", validate)
    graph.add_node("confirm_run", confirm_run)
    graph.add_node("run_simulation", run_simulation_node)
    graph.add_node("evaluate_results", evaluate_results)
    graph.add_node("summarize", summarize)

    graph.add_edge(START, "understand_request")
    graph.add_conditional_edges(
        "understand_request", route_after_understand, {"validate": "validate", "end": END}
    )
    graph.add_conditional_edges(
        "validate", route_after_validate, {"confirm": "confirm_run", "end": END}
    )
    graph.add_conditional_edges(
        "confirm_run", route_after_confirm, {"run": "run_simulation", "end": END}
    )
    graph.add_conditional_edges(
        "run_simulation",
        route_after_run,
        {"evaluate": "evaluate_results", "summarize": "summarize", "end": END},
    )
    graph.add_edge("evaluate_results", "summarize")
    graph.add_edge("summarize", END)

    return graph.compile(checkpointer=checkpointer)
