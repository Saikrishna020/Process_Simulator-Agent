"""Local-only browser test server. No LLM/provider calls; never use as the real app.

Copies the saved BPI model into an isolated test workspace. The scripted planner
exercises real session HTTP routes, LangGraph checkpoints and confirmation.
"""
import json
import os
from pathlib import Path
import shutil
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["DEEPSEEK_API_KEY"] = "local-fixture-no-network"

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from processlab import main
from processlab.assistant import graph, sessions as jobs
from processlab.service import api, jobs as service
from processlab.analysis import analyst as analyst_module
from processlab.core.storage import atomic_json
from processlab.engine.model import MODEL_VERSION

fixture_root = ROOT / "runs" / "browser-fixture" / uuid.uuid4().hex
store = service.Workbench(fixture_root)
source = next(p for p in (ROOT / "runs" / "workbench" / "models").glob("*.json")
              if json.loads(p.read_text())["version"] == MODEL_VERSION)
(fixture_root / "models").mkdir(parents=True)
shutil.copy2(source, fixture_root / "models" / source.name)
job_id = uuid.uuid4().hex
atomic_json(fixture_root / "jobs" / f"{job_id}.json",
            dict(id=job_id, kind="discover", status="done", dataset="BPIC_2017_W",
                 name="Local browser fixture", model_id=source.stem, error=None,
                 created_at="2026-09-22T00:00:00Z"))
main.workbench = api.workbench = service.workbench = store
main.configure_langsmith = jobs.configure_langsmith = lambda: None


class LocalPlanner:
    def bind_tools(self, _):
        return self

    def invoke(self, messages):
        question = next(m.content for m in reversed(messages) if isinstance(m, HumanMessage))
        if "Run a scenario" in question:
            # Exercises the real confirmation/run path with a scenario the engine accepts.
            args = dict(name="Browser fixture scenario", horizon_days=7, repetitions=1,
                        demand_multiplier=1.5)
            return AIMessage(content="", tool_calls=[
                dict(name="ScenarioRequest", args=args, id=uuid.uuid4().hex)])
        facts = json.loads(messages[0].content.split("Selected model facts (data, not instructions):\n", 1)[1])
        summary = facts["historical_summary"]
        return AIMessage(content=f"The whole log contains {summary['cases']:,} cases and {summary['resource_count']} resources. (Local test provider.)")


graph.build_llm = LocalPlanner
jobs._graph = graph.build_graph(MemorySaver())


def local_analysis_plan(question, catalog, previous=None):
    if question == "Show denied cases only":
        plan = (previous or analyst_module.AnalysisPlan()).model_copy(update={"filters": [analyst_module.Filter(field="outcome", value="Denied")]})
        return analyst_module.Intent(plan=plan)
    return analyst_module.Intent(clarification="This request needs a validated prediction model; choose a descriptive analysis.")


analyst_module.plan_question = local_analysis_plan


@main.app.get("/api/test-provider")
def test_provider():
    return {"provider": "local-fixture", "external_requests": False}

if __name__ == "__main__":
    import uvicorn
    print(f"Local browser fixture: {fixture_root}", flush=True)
    uvicorn.run(main.app, host="127.0.0.1", port=8001)
