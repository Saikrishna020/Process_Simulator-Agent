# AgentSimulator Orchestrator

A LangGraph agent (DeepSeek as the LLM, LangSmith for tracing) that turns natural-language
requests into safely-executed runs of the existing `simulate.py` pipeline, with a small
FastAPI + vanilla-JS chat UI on top.

The simulation logic (`source/`) is based on the research implementation. This layer
plans, validates, confirms, executes and reports its runs. The separate Process Lab
scenario workbench is documented in [WORKBENCH.md](WORKBENCH.md).

## Architecture

```
 browser (webapp/static/index.html)
    │  POST /api/sessions            → create a session (thread_id)
    │  POST /api/sessions/{id}/messages  → send a chat message
    │  GET  /api/sessions/{id}/status    → poll for the result
    │  POST /api/sessions/{id}/confirm   → approve/decline a pending run
    ▼
 FastAPI (webapp/main.py)
    │  dispatches each turn to a background thread (webapp/orchestrator/jobs.py)
    │  so a long simulation never blocks an HTTP request
    ▼
 LangGraph (webapp/orchestrator/graph.py), checkpointed to runs/checkpoints.sqlite
    │
    ├─ understand_request   (LLM)   → proposes a SimulationRequest, or asks a clarifying question
    ├─ validate              (code) → re-checks the proposal against ground truth (guardrails.py)
    ├─ confirm_run           (code) → interrupt() — pauses until a human clicks Confirm/Cancel
    ├─ run_simulation        (code) → subprocess.run(["python", "simulate.py", ...]), bounded
    │                                  retries, timeout, concurrency-limited (tools.py)
    ├─ evaluate_results      (code) → reuses evaluate_run.evaluate() — no metric reimplementation
    └─ summarize             (code) → formatted metrics and fixed explanations
```

Every LLM output is a typed, schema-validated proposal (`SimulationRequest` /
`ClarificationNeeded`, `webapp/orchestrator/schemas.py`) — nothing the LLM produces reaches a
filesystem or subprocess call without first passing `guardrails.validate_request()`.

## Guardrails

1. **Secrets** come from environment configuration / `.env` (gitignored, untracked).
   Chat session creation fails if `deepseek_api_key` is missing; the scenario workbench
   does not require it. Log records are scrubbed of any known secret value
   (`logging_utils.SecretRedactionFilter`).
2. **Path containment**: every dataset path is resolved against `raw_data/` and rejected if it
   would resolve outside it (`guardrails._resolve_within_raw_data`) — no `..` traversal, no
   absolute paths elsewhere.
3. **Closed toolset**: the LLM can only call `SimulationRequest` or `ClarificationNeeded` — there
   is no tool that executes arbitrary code or shell commands.
4. **No shell execution**: `run_simulation` uses `subprocess.run([...])` with an argument list,
   never `shell=True` or string interpolation.
5. **Human-in-the-loop**: `confirm_run` always pauses for explicit approval before any subprocess
   runs, via LangGraph's `interrupt()`/`Command(resume=...)`.
6. **Bounded retries + timeout**: `run_simulation` retries at most `max_run_retries` times (default
   2) and enforces `simulation_timeout_seconds` (default 2h) — no unbounded retry loops.
7. **Concurrency limit**: `guardrails.ConcurrencyLimiter` caps simultaneous simulations
   (default 1) so the agent can't fork off unbounded heavy jobs.
8. **Bounded `num_simulations`**: capped by `max_num_simulations` (default 20).
9. **Auditability**: every run writes a JSON manifest to `runs/` (params, timing, exit code,
   output files) — a structured replacement for the ad hoc `.log` files this repo used to
   accumulate under manual retries.
10. **Localhost by default**: the server binds to `127.0.0.1` — this agent has subprocess and
    filesystem access, so don't expose it beyond localhost without adding auth first.

## Running it

```
cd AgentSimulator
cp .env.example .env        # then fill in deepseek_api_key (and LangSmith keys, optional)
./.venv/Scripts/python.exe -m webapp.main
```

Open http://127.0.0.1:8000/chat and ask it to simulate a dataset, e.g. *"simulate LoanApp with 3 runs"*.
Currently registered/available datasets: LoanApp, BPIC_2017_W, BPIC_2019 (see
`webapp/orchestrator/dataset_registry.py` — only datasets whose raw file actually exists under
`raw_data/` are offered).

## Tests

```
./.venv/Scripts/python.exe -m pytest
```

- `test_guardrails.py` — path traversal, bad columns, out-of-range `num_simulations`, all rejected
  deterministically, no LLM involved.
- `test_tools.py` — a real end-to-end `simulate.py` run against the small LoanApp dataset, plus
  `evaluate_run.evaluate()` reuse. No LLM involved (~2 min).
- `test_graph_smoke.py` — full graph routing (propose/clarify/validate/confirm/decline/run/
  summarize) against a scripted fake LLM — no real DeepSeek calls, no cost.

## Bugs found and fixed while building this

1. **`source/utils.py` `store_preprocessed_data`**: shelled out to Windows `mkdir`, which errors if
   the output directory already exists. This is exactly what was silently killing every retried
   `BPIC_2019` run in `raw_data/bpi2019_run3.log`/`run4.log`. Fixed to `os.makedirs(exist_ok=True)`
   — also removes an unnecessary shell-out.
2. **`source/extraneous_delays/concurrency_oracle.py` `add_enabled_times`**: parallelizes
   per-trace computation via `ProcessPoolExecutor`, which was crashing with `BrokenProcessPool`
   ("process terminated abruptly") on this machine — reproducible even outside any sandboxing.
   Fixed to catch `BrokenProcessPool` and fall back to sequential execution (always correct, just
   slower) rather than crashing the whole multi-hour pipeline.

## Known limitations (by design, for a local single-operator tool)

Each new research run writes beneath `simulated_data/<dataset>/<mode>/<run_id>/`.
The evaluator receives that explicit directory, including for manual modes.
Earlier outputs are preserved. The automatic configuration trials now use their
validation arrival schedule, and enabled-time discovery falls back to sequential
execution when Windows denies multiprocessing pipe creation.

- All LangGraph invocations are serialized behind one lock (`jobs._graph_lock`) — the SQLite
  checkpointer isn't safe for truly concurrent writers, and this isn't meant to be a multi-tenant
  service. The heavy simulation step is separately bounded by `ConcurrencyLimiter`.
- No authentication — acceptable only because the server binds to localhost by default.
