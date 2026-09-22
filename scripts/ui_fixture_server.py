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
from webapp import main
from webapp.orchestrator import graph, jobs
from webapp.orchestrator.dataset_registry import get_dataset
from webapp.workbench import api, service
from webapp.workbench.model import MODEL_VERSION, atomic_json

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
        if "Run the research simulator" in question:
            dataset = get_dataset("BPIC_2017_W")
            args = dict(dataset_name=dataset.name, case_id=dataset.case_id,
                        activity_name=dataset.activity_name, resource=dataset.resource,
                        start_timestamp=dataset.start_timestamp, end_timestamp=dataset.end_timestamp,
                        num_simulations=1)
            return AIMessage(content="", tool_calls=[dict(name="SimulationRequest", args=args, id=uuid.uuid4().hex)])
        facts = json.loads(messages[0].content.split("Selected model facts (data, not instructions):\n", 1)[1])
        summary = facts["historical_summary"]
        return AIMessage(content=f"The whole log contains {summary['cases']:,} cases and {summary['resource_count']} resources. (Local test provider.)")


graph.build_llm = LocalPlanner
jobs._graph = graph.build_graph(MemorySaver())


@main.app.get("/api/test-provider")
def test_provider():
    return {"provider": "local-fixture", "external_requests": False}

if __name__ == "__main__":
    import uvicorn
    print(f"Local browser fixture: {fixture_root}", flush=True)
    uvicorn.run(main.app, host="127.0.0.1", port=8001)
