# Process Lab

**Learn how an organisation really works from its event log — then simulate "what if?" and see
which changes actually matter.**

Process Lab reads an event log (which case, what activity, who did it, when it started, when it
ended), learns a working model of the organisation from history — who can do what, how fast,
when they work, who hands work to whom, how fast cases arrive — and lets you run paired
what-if experiments against a historical baseline.

Built on the real **BPI Challenge 2017** log: 31,500 loan applications, 149 people, one year of
a Dutch bank's lending process.

```powershell
./start.ps1          # Process Lab at http://127.0.0.1:8000 — no API key needed
```

---

## What the data actually says

1. **A typical case takes 19.1 days but needs only ~25 minutes of hands-on work** — 0.5% of
   elapsed time. The rest is waiting.
2. **Most waiting is not a staffing problem.** About 78% of simulated cycle time is external
   delay (customer callbacks, documents); ~21% is queueing; processing is ~1%. Simulated
   utilisation is ~6%.
3. **Adding or removing people barely moves cycle time.** Removing the busiest person: −0.2%.
   Adding three copies of them: −0.2%. Halving all external delays: **−37%**.
4. **How work is allocated does matter.** Sending each task to the earliest available qualified
   person instead of the historically preferred one cuts queue wait by 96% (61 h → 2 h) and
   cycle time by ~18%. This is an upper bound — the log cannot show real skill or approval limits.
5. **A third of cases are customer cancellations, and those are the slow ones.** 33% end in
   cancellation with a median cycle time of 31.6 days, versus 14.8 (approved) and 14.1 (denied).
   The median wait for a customer to respond to an offer is 185 hours (~7.7 days).

Every one of these is reproducible live in the app: **Data explorer → Data Analyst**, where the
preset questions run locally with no API key.

---

## One engine, deliberately

This project previously shipped **two** simulation engines: the published *AgentSimulator*
research engine (`source/`, Mesa-based, agent-per-resource) and this empirical engine. They have
been consolidated, and the reasoning is worth stating because it is the central design decision.

| | Research engine (removed) | **Process Lab engine** |
|---|---|---|
| Runtime | minutes → hours | **~10 s per paired comparison** |
| **Can it run what-ifs?** | **No** — it cannot change staffing, schedules, demand or speed | **Yes; that is its purpose** |
| Paired random numbers | No | **Yes — an unchanged scenario reproduces the baseline exactly** |
| Inspectable model | No | **Yes — immutable, content-hashed, readable JSON** |
| Models parallel branches | Yes | No |
| Generates *novel* control flow | Yes | No — it resamples frozen real traces |

The deciding argument is not speed. **A what-if tool whose engine has no what-if levers cannot
answer the product's question.** The research engine's role was to produce a benchmark table; that
capability was kept instead by porting its five published measures onto this engine
(`processlab/scoring/`), so the engine that actually makes the claims is now the engine that gets
scored on the standard yardstick.

**What was given up, honestly:** parallel-branch modelling, the ability to simulate activity
sequences that never occurred, and a fix (plus its test) for a `BrokenProcessPool` crash in the
paper's enabled-time discovery, which was only relevant to that code. The paper remains credited
below; its method shaped this one.

---

## How it works

### Learning — `processlab/engine/model.py`

1. **Validate** the log: no missing identifiers, no negative durations, at least ten cases, and
   **refuse an all-zero-duration log** outright (see BPI 2019 below).
2. **Temporal split** at the 80th-percentile case start (BPI 2017: 19 Oct 2016). Training cases
   must *finish* before the cutoff (24,041); held-out cases must *start* at or after it (6,300);
   **1,159 straddlers are excluded** — a half-observed case teaches wrong durations and a wrong
   ending. Never a random split: processes drift, and a random split leaks the future. The rule
   lives in `engine/split.py` and is shared with scoring so the two cannot diverge.
3. **Per resource** (137 profiles): a calendar inferred from the earliest-to-latest observed hour
   per weekday in **UTC**; per-activity **empirical working-time samples** with off-calendar time
   subtracted; median/mean/p90; observed load; top-5 handoff probabilities.
4. **Trace templates**: every training case frozen as `[activity, preferred resource, residual
   delay]`. Residual delay is measured from the *first feasible working window after the case was
   ready* to the observed start — see the bug below, which turned on exactly this.
5. **Arrivals**: per weekday, the real list of per-day arrival-time offsets. A simulated day picks
   a historical day of that weekday and reuses its arrivals.

### Simulating — `processlab/engine/simulation.py`

A ready-time priority queue: pop the earliest-ready task, choose a resource, work it inside that
person's calendar (pausing at shift end and weekends), release them, schedule the next task at
`end + residual delay`.

- **Allocation** — `historical`: the template's preferred resource or a copy of it, else any
  qualified peer; `pooled`: always the earliest-available qualified resource.
- **Duration** — a seeded quantile lookup into that resource's empirical samples, times the
  resource and activity multipliers. Sampling the observed list means no distributional
  assumption and rare long tasks keep their real frequency.
- **Paired design (common random numbers)** — arrivals, template choice and per-task quantiles all
  derive from `seed` and the case index, so baseline and scenario share draws and their
  *difference* has far lower variance than two independent runs. An unchanged scenario reproduces
  the baseline exactly; a test enforces it.

**Levers:** clone a resource (×1–20, optional custom schedule/speed), remove one (rejected if it
strands an activity), change a resource's speed or hours, scale an activity's processing time or
its residual delay, scale demand (25–300%), set horizon (7–90 days), repetitions (1–10), deadline,
seed and allocation rule.

**Caps:** ≤50 added resources, ≤30,000 cases / 300,000 events per repetition, a 15-minute budget,
one background job at a time, and a backlog beyond five years is rejected rather than spun on.

### Scoring — `processlab/scoring/metrics.py`

The five published measures from the AgentSimulator paper, against the real held-out log. On
BPI 2017 (2 baseline repetitions, 78-day held-out arrival window, 6,300 cases / 47,060 events):

| | Measure | Mean | Reading |
|---|---|---|---|
| **NGD** | 3-gram activity sequences | 0.167 | **qualified** |
| **AEDD** | absolute calendar placement | 147.8 | **qualified** |
| **CEDD** | hour-of-day / day-of-week rhythm | 2.17 | informative |
| **REDD** | timing relative to case start | 30.3 | informative |
| **CTDD** | overall case durations | 53.2 | informative — the headline |

**Two of the five are labelled "qualified" on purpose.** This engine replays *real observed
traces*, so NGD largely measures train-vs-test control-flow drift in the data rather than model
skill — a good NGD here is close to a tautology, and quoting it as an achievement would be
dishonest. AEDD depends on arrival-horizon alignment, so the real log is trimmed to the simulated
window to make the populations comparable at all. **CEDD, REDD and CTDD are the informative
three**, and CTDD is the one that reflects queueing and delay together. The API and the CLI both
return these caveats next to the numbers:

```powershell
python scripts/score_baseline.py BPIC_2017_W
```

These are *not* like-for-like with the research engine's old table (different held-out population,
different arrival treatment), so they are not a victory claim over it. They are the first time the
engine that produces the findings has been measured on the standard yardstick.

---

## Architecture

```
processlab/
  core/        config, dataset registry, LLM client, crash-safe storage, log redaction
  engine/      split · calendar · model · simulation · schemas   <- the one engine
  extract/     streaming XES -> CSV (full log, case outcomes)
  analysis/    the typed-plan Data Analyst
  scoring/     the five published distance measures
  service/     bounded job worker · REST API
  assistant/   LangGraph agent: schemas · guardrails · tools · graph · sessions
  web/static/  one single-page app, no framework, no build step
  main.py      FastAPI: "/" is Process Lab; "/chat" redirects to the assistant tab
```

`core/` exists to break an inverted dependency the old layout had: the no-credentials product
layer used to import *from* the LLM layer. Now both depend on shared infrastructure, and the LLM
key is **optional** — the engine, scenarios and all ten analyst presets never touch it.

### The Data Analyst: the LLM plans, code calculates

Data explorer opens with questions rather than a fixed dashboard.

- **Ten preset questions** run entirely locally — no key, no network. They short-circuit before
  the planner is even imported.
- Free-text questions use the LLM **only to fill in a typed `AnalysisPlan`** (table, metric,
  statistic, grouping, filters, chart). The planner receives the question, the field catalog and
  the previous plan — **never case records, never computed results**. External tracing is disabled
  for that call.
- **No generated code, SQL, paths or formulae are ever executed**, and no model-produced number is
  trusted. Execution is plain pandas over an allow-listed schema.
- Every result carries its query, population, per-group sample sizes, missingness, source
  fingerprints and automatic warnings ("descriptive association, not causation", "some groups have
  fewer than 30 observations", "showing 20 of 48 groups"). Missing values stay missing — they never
  silently become zero.
- Unsupported requests — predictions, causal claims, optimisation — return a **clarification**, not
  an invented chart. A test enforces that.

### The Simulation assistant: propose, validate, confirm, run

```
understand_request (LLM) → validate (code) → confirm_run (human) → run_scenario → report
```

The assistant used to be able only to trigger a research run and apologise that it could not change
staffing. Pointed at this engine, it runs real scenarios.

1. **Closed toolset** — the LLM may emit exactly two typed objects, `ScenarioRequest` or
   `ClarificationNeeded`. There is no tool that executes code or a shell.
2. **The LLM names people; code resolves them.** Proposals are *name-based* ("User_87",
   "W_Call after offers"); internal ids are never invented by the model.
   `guardrails.validate_scenario()` resolves names against the selected model, case-insensitively
   and tolerating BPI 2017's real trailing-space activity name, and rejects anything unmatched
   **with a close-match suggestion rather than a silent substitution**.
3. **Reuses the engine's own rules** rather than duplicating them: `ExperimentRequest`'s validators
   for the caps, and `scenario_resources()` for unknown ids and removals that strand an activity.
4. **Human-in-the-loop** — `confirm_run` is a real LangGraph `interrupt()`; the graph suspends until
   someone clicks Confirm. Nothing has run at that point.
5. **Re-validated immediately before execution**, not just at proposal time.
6. **Bounded** — concurrency limited to one in-flight experiment, a wall-clock timeout, and the
   service's own single bounded worker underneath.
7. **Auditable** — every run writes a JSON manifest under `runs/assistant/`.
8. **The model cannot redirect the run.** `ScenarioRequest` has no `model_id` field; the model comes
   from session state.
9. **Secrets** come only from `.env`, and every log record is scrubbed by a redaction filter.
10. **Localhost by default** — the process has filesystem access and there is no auth.

State is checkpointed to SQLite, so a conversation survives a restart. LangSmith tracing is optional.

---

## Honest limitations

Say these first; they make the rest credible.

- **Not a forecast.** Against a censoring-corrected reference the baseline is **+12% mean, +3%
  median, +18% p90**. Use it to *compare scenarios*, not to predict days.
- **One task at a time per resource** — yet **15.4%** of real BPI 2017 work items overlap. The app
  reports that overlap so the assumption stays visible. No batching, fatigue or learning curves.
- **No parallel branches within a case.**
- **Calendars are inferred, in UTC.** "Earliest to latest observed hour per weekday" is not a
  roster; one Sunday-evening event grants someone a Sunday window. Dutch local time is UTC+1/+2.
- **Cloning a person copies that person's behaviour.** A real new hire would differ; the chosen
  source is the explicit assumption.
- **Residual delay is a residual, not a diagnosis.** It bundles customer delay, approvals,
  unobserved work and model error.
- **Short horizons mislead "backlog"** — the simulation starts with an empty queue, so a 7-day run
  reports most cases as backlog. Use ≥30 days.
- **Utilisation ~6% is relative, not an absolute staffing figure.**
- **Process steps cannot be added or removed** — only resources, times, delays, demand and routing.
- **Pooled routing is an upper bound** (no skill tiers in the log).
- **Local single-operator tool** — no authentication, one worker.

---

## Two validation stories

### A bug caught by sanity-checking the model against the data

Model v1 estimated residual delay from the **last resource completion** before a task's observed
start. That silently converted customer waiting into resource contention.

| | v1 (wrong) | v2 (fixed) |
|---|---|---|
| Mean queue & calendar wait per case | **405 h** | 71 h |
| Mean residual delay per case | **6.9 h** | 263 h |
| Median simulated cycle time | 37 h | 239 h (held-out history: 219 h) |

Two things exposed it: the simulated median (37 h) was nowhere near the held-out median (219 h),
and "add capacity" should have helped enormously if queues really were 405 h — it didn't. v2 takes
the *first feasible working window after the case was ready*.
`test_delay_uses_first_idle_window_not_the_last_completion` locks it in, and `MODEL_VERSION` was
bumped so v1 snapshots are refused rather than silently reused.

**It also inverted the business conclusion.** Under v1 capacity looked like the bottleneck; under
v2, 78% of cycle time is external and staffing barely matters. That is the project's whole thesis,
and it turned on one wrong line.

### Why the baseline looked 20% off, and why it isn't

**The real number was measured unfairly.** Held-out cases start after 19 Oct 2016, but the log stops
1 Feb 2017, so late-arriving cases are cut short and look faster; the simulator runs every case to
completion. Dropping "unfinished" cases makes it worse, not better — the slow cases are exactly the
ones still open, and the log has no "finished" flag. Instead the app keeps held-out cases that *had
enough time to finish*: at least as much follow-up as 99.5% of training cases needed (62 days;
3,937 of 6,300 cases). If too little history remains it falls back to all held-out cases **and says
so**.

**Against that fair reference the baseline is +12% / +3% / +18%.** The median is right; the mean and
tail are too slow. The validation panel shows why: three people hold ~48% of all simulated queueing
(User_121 alone 25%) despite an observed load of 18–47%. Their typical task is ~2 minutes, but 79%
of User_121's recorded working time sits in the slowest 1% of tasks. Across the log, **195 of
195,564 recorded tasks (0.1%) hold 38% of all working time; the longest is 531 working hours** —
almost certainly work items left open and resumed weeks later. Capping durations at 24 working hours
*in memory* turns the gap into −2.7% / −4.7% / −6.2% and drops the top-3 queue share to 14%.

**The cap is deliberately not shipped.** It over-corrects, because the elapsed weeks vanish
entirely. The correct fix caps *working* time and reassigns the excess to residual delay, which
changes every headline number — so it belongs in a deliberate model-version bump, not a quiet patch.

```powershell
python scripts/check_baseline_censoring.py    # the censoring analysis
python scripts/diagnose_baseline_gap.py       # the outlier analysis
```

---

## Bugs the consolidation surfaced

Running the browser check for real — rather than trusting that it passed — found three stale
assertions that had been silently green, plus two of my own.

**Already broken before the consolidation,** because the browser script was never updated when
the explorer moved to the full application/offer/work-item log (model version 4):

1. **The explorer count assertion checked 31,500** — the `W_`-only case count — while the page
   correctly rendered **31,509**, the full-log count. The nine applications with no work item at
   all appear in the full log but not the `W_` subset.
2. **The assistant reply assertion had the same hardcoded 31,500**, for the same reason.
3. **The chart assertion clicked preset `[8]`** expecting the monthly line chart. When a tenth
   preset ("Outcome mix by resource") was inserted ahead of it, index 8 became a *bar* chart — so
   the assertion kept passing while testing the wrong thing.

All three are now derived from the model's own figures or selected by name, so they cannot go
stale again: a hardcoded expectation that drifts from the code is worse than no test, because it
reports success.

**Introduced by the consolidation and caught the same way:**

4. **A startup log line could stop the app serving.** The new lifespan called `get_settings()` to
   report whether the assistant was enabled; if settings failed to resolve, boot failed. A log
   line must never decide whether the app runs.
5. **An orphaned bullet in the confirmation card.** The card's value cells are right-aligned, so
   the change list's markers stranded themselves at the far left, away from their text — visible
   only in a screenshot, not in any assertion.

## Data

| | **BPIC_2017_W** | BPIC_2019 | LoanApp |
|---|---|---|---|
| Cases / events | **31,500 / 254,382** | 251,734 / 1,595,923 | 1,000 / 7,492 |
| Resources | 149 | 628 | 19 |
| Real durations? | **Yes** | **No — 100% zero-duration** | Yes |
| In the product? | **Yes** | Disabled, with a stated reason | No — test fixture only |

The raw BPI 2017 XES is 578 MB and 1.2M events in three families: `A_*` application states, `O_*`
offer states and `W_*` work items. Only `W_*` are human work with durations, and XES stores each as
several lifecycle events (`schedule`, `start`, `suspend`, `resume`, `complete`, …). The converter
**pairs `start`/`resume` → `complete`/`suspend` into intervals**, discarding the rest, streaming the
whole file at constant memory → 254,382 real intervals.

Two further extractions are kept deliberately separate from the simulation model:
`extract/outcomes.py` (case outcomes, rework, offers, customer response time) and
`extract/full_log.py` (all three families, for the explorer's charts). `discover()` takes the full
log as an **optional, descriptive-only** argument — it never touches resource profiles, calendars,
templates or arrival days, so the capacity model is unaffected by whether a full log exists. A test
enforces that.

**BPI 2019 is kept as a data-quality lesson.** Every event is zero-duration, 25% have resource
`NONE`, and timestamps reach back to 1948. Without durations you cannot learn how long someone takes
or how busy they are, so the engine refuses it outright. *Fit-for-purpose data checks come before
modelling* — a simulator that can't tell a log has no durations will silently produce nonsense.

---

## API

```
GET  /api/workbench/datasets
POST /api/workbench/models                      {"dataset": "BPIC_2017_W"}
GET  /api/workbench/models/{id}                 public profiles; excludes raw samples/templates
GET  /api/workbench/models/{id}/score           the five published measures + caveats
GET  /api/workbench/models/{id}/analyst         field catalog and preset questions
POST /api/workbench/analyses                    a question, or an explicit typed plan
GET  /api/workbench/analyses/{id}
POST /api/workbench/experiments                 ExperimentRequest
GET  /api/workbench/jobs  ·  /jobs/{id}
GET  /api/workbench/experiments/{id}
GET  /api/workbench/experiments/{id}/files/{filename}
POST /api/sessions  ·  /{id}/messages  ·  /{id}/confirm  ·  GET /{id}/status
```

OpenAPI docs at `/docs`.

### Saved artifacts

```
runs/workbench/models/<model_id>.json        immutable learned model, source hash, version
runs/workbench/jobs/<job_id>.json            durable progress / outcome
runs/workbench/experiments/<job_id>/         request.json, result.json, baseline_N.csv, scenario_N.csv
runs/workbench/analyses/<id>.json            saved analyst results
runs/assistant/<run_id>.json                 assistant run manifests
runs/checkpoints.sqlite                      LangGraph conversation state
```

Model ids are content hashes of `MODEL_VERSION : dataset : file_hash`, so an unchanged source file
is an instant cache hit and a changed one is a different model. Nothing is ever overwritten.
Interrupted jobs are marked as errors on restart and can be resubmitted.

---

## Engineering notes

- Typed boundaries throughout: Pydantic with `extra="forbid"` and `allow_inf_nan=False`, plus
  cross-field validators (schedule end after start, unique weekdays, one change per
  resource/activity, every numeric range).
- Crash-safe Windows-aware persistence: `atomic_json` writes a temp file and retries `replace()`
  against transient locks rather than truncating a file a reader holds; `read_json` retries
  symmetrically. There is a regression test for it.
- Bounded everywhere: one scenario worker, two analyst slots, one assistant experiment, a 15-minute
  comparison budget, and explicit size caps.
- Defence in depth on outputs: regex-validated job ids and filenames, bounded repetition indices,
  and `public_model()` stripping samples and templates from anything served over HTTP.
- **76 tests, all green in under six seconds**, run by GitHub Actions on every push. The end-to-end
  scenario now executes on the engine in-process — the old research-engine equivalent took ~2
  minutes and CI had to skip it. The assistant is tested against a scripted fake LLM: full routing
  coverage, no provider calls, no cost.
- Browser verification runs against a local fixture server with a scripted planner, so UI checks
  make no external LLM requests:
  ```powershell
  python scripts/ui_fixture_server.py                       # terminal 1
  python scripts/verify_workbench_ui.py --local-assistant    # terminal 2 (needs Edge)
  ```

```powershell
python -m pytest -q
node --check processlab/web/static/workbench.js
```

---

## Background reading

[docs/GUIDE.md](docs/GUIDE.md) — process mining in ten minutes, what the three datasets contain,
the full BPI 2017 data exploration, and a glossary. Deliberately engine-agnostic; this README
remains the single source of truth for architecture, method and results.

## Credits

The method — modelling each resource as an agent with its own schedule, skills, speed and handover
habits, and letting the process emerge from their interaction — comes from **AgentSimulator**:
*"AgentSimulator: An Agent-based Approach for Data-driven Business Process Simulation"* by Lukas
Kirchdorfer, Robert Blümel, Timotheus Kampik, Han van der Aa and Heiner Stuckenschmidt, ICPM 2024
([arXiv 2408.08571](https://arxiv.org/abs/2408.08571)). Earlier versions of this repository ran that
paper's supplementary code directly; this engine, the scoring harness, Process Lab, the Data Analyst,
the assistant and its guardrails are built on top of its ideas rather than its implementation.

The five distance measures come from the `log-distance-measures` package. Residual-delay discovery
follows Chapela-Campa & Dumas.

MIT licensed — see [LICENSE](LICENSE).
