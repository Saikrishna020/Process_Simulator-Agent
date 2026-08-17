# Process Simulator Agent

A chat-based agent for running business process simulations: describe what you want in plain
English ("simulate LoanApp with 3 runs"), the agent plans a validated simulation request, a human
confirms it, then it runs the simulation and explains the results.

Under the hood it's a LangGraph agent (DeepSeek as the LLM) orchestrating an agent-based business
process simulation engine — given a real event log, it learns how work moves between resources
(who does what, how tasks hand off, typical timing) and generates synthetic event logs with the
same statistical behavior, then scores how realistic they are against held-out real data.

See [ORCHESTRATOR.md](ORCHESTRATOR.md) for the full architecture, guardrails, and how to run it.

## Guardrails, briefly

Nothing the LLM proposes is trusted directly — every simulation request it produces is
deterministically re-validated (dataset exists, columns exist, paths can't escape `raw_data/`)
before it's allowed to touch a subprocess or the filesystem, and every run pauses for explicit
human confirmation before executing. Full detail in [ORCHESTRATOR.md](ORCHESTRATOR.md#guardrails).

## Credits

The simulation engine (`source/`, `simulate.py`) is built on top of **AgentSimulator**, the
supplementary code for the paper *"AgentSimulator: An Agent-based Approach for Data-driven
Business Process Simulation"* by Lukas Kirchdorfer, Robert Blümel, Timotheus Kampik, Han van der
Aa, and Heiner Stuckenschmidt. The chat agent, guardrails, and web app layer (`webapp/`) are
built on top of that engine.

MIT licensed — see [LICENSE](LICENSE).
