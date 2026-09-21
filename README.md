# Process Lab / AgentSimulator

**Learn how an organisation really works from its event log — then simulate "what if?" and
see which changes actually matter.**

![Process Lab results: pooled allocation vs. historical baseline on BPI 2017](docs/screenshots/results.png)

## What I found (BPI Challenge 2017, 31,500 loan applications, 149 people)

1. **A typical case takes 9.7 days but needs only ~25 minutes of hands-on work** — 0.9% of
   elapsed time. The rest is waiting.
2. **Most waiting is not a staffing problem.** About 78% of simulated cycle time is external
   delay (customer callbacks, documents); ~21% is queueing; processing is ~1%.
3. **Adding or removing people barely moves cycle time.** Removing the busiest person: −0.2%.
   Adding three copies of them: −0.2%. Halving all external delays: **−37%**.
4. **How work is allocated does matter.** Sending each task to the earliest available qualified
   person, instead of the historically preferred one, cuts queue wait by 96% (61 h → 2 h) and
   cycle time by ~18%. This is an upper bound: the log cannot show real skill or approval limits.

Full analysis, numbers and reproduction steps: **[PROJECT_GUIDE.md](PROJECT_GUIDE.md)**.

## Honest limitations

- **Not a forecast.** With no changes the simulated baseline reads ~20% slower than held-out
  history (337 h vs 281 h), but about half of that is the real log ending early, which cuts late
  cases short. Against a fair reference the gap is +8% mean, ~0% median and +14% at the tail (a
  few overloaded people build unrealistic queues). Use it to *compare* scenarios, not to predict
  days. Evidence: [WORKBENCH.md](WORKBENCH.md) and `scripts/check_baseline_censoring.py`.
- One task at a time per resource (15% of real work items overlap), no batching or fatigue.
- Working hours are inferred from observed activity and shown in UTC.
- Cloning a person copies the original's behaviour; a real new hire would differ.
- Residual delay is a leftover, not a diagnosed cause.

## Try it

```powershell
cd AgentSimulator
./start-workbench.ps1          # Process Lab at http://127.0.0.1:8000, no API key needed
```

Pick **BPIC_2017_W** → **Learn resource profiles** → open **Scenario builder** and start from a
preset (pooled allocation, halve delays, remove the busiest person, demand +50%). Each run
compares a paired baseline and scenario and saves settings, seeds and downloadable event logs.
See [WORKBENCH.md](WORKBENCH.md) for the method, assumptions, API, and a write-up of a bug caught
by sanity-checking the model against held-out data.

## What is in this repo

| Part | What it does | LLM key? |
| --- | --- | --- |
| **Process Lab** (`webapp/workbench/`, served at `/`) | Learns resource profiles from history, runs paired baseline/scenario comparisons. Empirical trace-resampling engine, inspectable and fast (~10 s per comparison). | No |
| **Research engine** (`source/`, `simulate.py`) | Multi-agent simulator from the AgentSimulator paper; each resource is an agent. Scored against held-out data with five distance metrics (`evaluate_run.py`). | No |
| **Chat agent** (`webapp/orchestrator/`, at `/chat`) | LangGraph + DeepSeek agent that turns "simulate LoanApp with 3 runs" into a validated, human-confirmed run of the research engine. See [ORCHESTRATOR.md](ORCHESTRATOR.md). | Yes |

Research engine scores (10 simulations, lower is better) — LoanApp reproduces the paper; BPI 2017
is comparable on control-flow but ~3× worse on cycle-time distance (different log extraction, see
the guide):

| Log | NGD | AEDD | CEDD | REDD | CTDD |
| --- | --- | --- | --- | --- | --- |
| LoanApp (this repo / paper) | 0.080 / 0.07 | 2.97 / 2.78 | 0.221 / 0.21 | 1.38 / 1.34 | 1.60 / 1.49 |
| BPI 2017 W (this repo / paper) | 0.225 / 0.30 | 234.6 / 221 | 2.37 / 1.64 | 66.9 / 26.0 | 69.8 / 22.8 |

## Engineering notes

- Temporal 80/20 split, paired random numbers, an unchanged scenario reproduces the baseline exactly.
- Typed, strictly validated requests; immutable content-hashed models; one bounded background job
  worker with crash recovery; nothing is overwritten.
- The chat agent never executes anything the LLM proposes directly: schema validation, path
  containment, human confirmation, no shell, bounded retries/timeouts.
- 28 tests (`python -m pytest`), including a full LoanApp run of the research engine.
- Data: `raw_data/` is not tracked (BPI logs are large). Convert the public BPI 2017 XES with
  `raw_data/xes_to_csv.py --w-only`. BPI 2019 is listed but disabled in the Lab because its
  export has only zero-duration events.

## Credits

The simulation engine (`source/`, `simulate.py`) builds on **AgentSimulator**, the supplementary
code for *"AgentSimulator: An Agent-based Approach for Data-driven Business Process Simulation"*
by Lukas Kirchdorfer, Robert Blümel, Timotheus Kampik, Han van der Aa and Heiner Stuckenschmidt
([arXiv 2408.08571](https://arxiv.org/abs/2408.08571)). Process Lab, the chat agent, guardrails,
and the web layer are built on top of that engine.

MIT licensed — see [LICENSE](LICENSE).
