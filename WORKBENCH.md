# Process Lab: history-based resource experiments

Process Lab learns resource profiles from an event log and compares a historical
baseline with a changed resource setup. It runs locally, without an LLM API key.
The data and research simulation chat agent lives in the same app, as the **Simulation assistant**
tab in the sidebar (`/chat` still redirects there for old links).

The assistant receives aggregate statistics from the selected model, plus at most ten
resource profiles (named resources in the question, otherwise the busiest). It can answer
historical-data questions without running a simulation. Raw case records and duration
samples are not included in this context. Model context is sent to the configured DeepSeek
provider when you send a message. Staffing, schedule and delay changes still use Scenario
builder; chat's run tool executes the separate research engine after confirmation.

### Explorer and chat troubleshooting

- Restart the Python server after changing backend code. A server running an older model
  version can reject a newer saved snapshot even though the browser loads the latest HTML.
- Start with `./start-workbench.ps1` in a normal terminal. If chat reports a connection
  failure, check the server process's proxy/network configuration. A dead loopback proxy
  inherited by a background process must be fixed in its launch environment, not in the API key.
- Model and job writes retry transient Windows file locks. The explorer refreshes after
  asynchronous model loading, and tab links survive reloads.
- Chat preserves the conversation in this browser, resumes polling after reload, and
  exposes a retry button on HTTP failures. After a server restart, retry opens a new
  session; previous displayed messages are not restored as the new session's LLM memory.
- Provider calls have a 30-second timeout and one retry. Research simulations have their
  own longer execution limit. While a plan awaits confirmation, confirm or cancel it
  before sending another message.

Browser verification without external LLM requests uses an isolated fixture workspace:

```powershell
# Terminal 1: scripted local planner, real HTTP/session/graph/scenario code
./.venv/Scripts/python.exe scripts/ui_fixture_server.py
# Terminal 2: requires installed Microsoft Edge
./.venv/Scripts/python.exe scripts/verify_workbench_ui.py --local-assistant
```

The test exercises whole-log explorer counts, chat navigation and reload, model-aware
replies, confirmation/cancellation, failed-request recovery, and scenario CSV export.
It does not evaluate the real provider's response quality. `--assistant` instead targets
the real app/provider and sends test prompts with selected-model summary data externally.

## Start

From `AgentSimulator` in PowerShell:

```powershell
./start-workbench.ps1
# Or:
./.venv/Scripts/python.exe -m webapp.main
```

Open **http://127.0.0.1:8000**. Use one server process/worker. The worker limit and
job lock are process-local. This is a local, single-operator application.

1. Choose **BPIC_2017_W** and click **Learn resource profiles**. Cached snapshots
   load immediately when the source file and model version have not changed.
2. Inspect a resource's activities, median working times, inferred calendar and
   frequent handoffs. The model is immutable; edits only affect the scenario.
3. Add copies of a resource, remove a resource, change its processing times or
   working hours. New resources share the source's historical work allocation but
   each has independent capacity. A new resource's personal behavior cannot be
   inferred without history; its chosen source is the explicit assumption.
4. In **Scenario builder**, set demand, arrival horizon, repetitions, deadline and
   allocation rule. Optionally change an activity's processing time or residual
   delay. `80%` processing time means a 20% duration reduction. A copy inherits
   the original historical profile, not other scenario edits, unless its custom
   working hours are explicitly selected.
5. Run the comparison. Read the metrics, activity waiting times and utilization.
   Export JSON and a CSV for any repetition. Experiment history can restore the
   settings of a saved comparison. Draft changes survive a page refresh locally.

BPIC_2017_W is the only dataset offered here. The current BPI 2019 CSV has
zero-duration events and is disabled in the selector because it cannot teach resource
processing capacity. LoanApp (the paper's small synthetic log) is registered but not
offered in the product — real data makes a stronger case, and LoanApp is kept only as a
fast, deterministic fixture for the test suite.
Arbitrary file upload and process-step creation/removal are not part of this version.

## Model and simulation method

The new workbench uses an **empirical trace-resampling model**, separate from the
research engine's next-activity probability model. It uses the same event-log
concepts but is designed for quick, inspectable capacity experiments.

- Source cases are ordered by arrival. The 80% case-arrival cutoff defines a
  temporal train/test split. Training cases must finish before the cutoff; test
  cases start at or after it. Cases crossing the cutoff are excluded. Test data
  does not supply profiles, paths, durations or delays.
- Every observed training resource gets a stable ID within the snapshot. Activity
  capabilities come from observed work. For each weekday, the earliest observed
  start/completion hour to the end of the latest hour defines a calendar window.
  These are observed windows in **UTC**, not verified employment rosters.
- Each resource/activity combination retains its empirical working-time samples.
  Off-calendar time is subtracted from historical elapsed processing time. Exact
  empirical frequencies are retained, including rare observations.
- Complete historical activity sequences and preferred resources form trace
  templates. Residual delays are estimated from the first feasible resource/calendar
  window after the preceding case work to the observed task start. These estimates
  are not labels for the actual causes of delay.
  Full-log outcome extraction now supplies direct evidence of long offer-response
  intervals: the matched BPI 2017 population has a median **185.4 hours** across
  **21,768 cases** with observed sent/returned offers. **9,732 cases** have no
  matched response and are excluded from this statistic. This supports investigating
  response delays; it does not identify every residual delay as customer waiting or
  prove that reducing these intervals would cause a particular outcome.
- Arrival-day counts and within-day times are sampled by weekday. Demand scales
  the sampled daily case count (rounded to an integer). Each case samples a trace
  template. The baseline and scenario share reproducible draws for matching cases.
- A ready-time priority queue schedules work. Resources handle one task at a time;
  tasks pause outside their calendars. Historical allocation uses the template's
  original resource and any copies. If it is removed, qualified peers become
  eligible. Pooled allocation always selects the earliest available qualified resource.
- All cases arriving within the chosen horizon are executed to completion. The
  simulation starts with an empty queue, and does not add arrivals after the horizon.

This runner cannot discover new process rules, simulate parallel branches within
a case, batching, fatigue, learning curves, or employee multitasking. It reports
the observed resource-overlap percentage so that the single-task assumption is
visible. Costs are not inferred from event logs. Cloning a resource does not
establish what a real new hire would do.

## Reading results

### Data Explorer as an analyst

Data Explorer now starts with questions rather than a fixed dashboard. The
suggested (preset) questions run entirely locally, without an API key — each is
a pre-built `AnalysisPlan` that skips the language planner entirely. Free-text
questions use DeepSeek only to produce a validated `AnalysisPlan`:
table, metric, statistic, grouping, filters and chart. The planner receives the
question, field catalog and optional previous query, not case records or computed
results. External tracing is disabled for this planner call. The backend never
executes generated Python, SQL, paths or formulae.

Supported analyses include counts and shares, mean/median/p90 durations, outcome
comparisons, rework and offer bands, observed customer-response hours, monthly
trends, activity gaps, and resource workloads. Queries can filter one or several
categories and numeric/date ranges. A second grouping includes both overall shares
and shares within the primary group. Bar charts, monthly lines and exact tables
come from the same computed rows. Missing values stay missing; small groups and
truncated group lists are explicitly identified.

Every result includes its query, population, sample sizes, missingness, definitions
and source fingerprints. Analyses are saved under `runs/workbench/analyses/`, can
be reopened, and can be downloaded as JSON or CSV. Two background analysis slots
bound concurrent work; parsed data is cached with source-change detection. A
server restart marks interrupted analyses for retry. Follow-up questions use the
last displayed query; “Start a fresh question” clears that context.

The analyst can suggest the next investigation and take the user to Scenario
builder. It **does not train an outcome predictor or claim an optimal intervention**.
Requests outside the supported schema should return a clarification, not invented
forecasts. The language planner can misunderstand a question, so the executed query
is always inspectable in the result; if it's wrong, rephrase the question or start
from one of the preset questions, which need no language model at all.

An earlier version also exposed the underlying `AnalysisPlan` fields (population,
measure, statistic, grouping, filters, chart) as a manual "Build an analysis
without AI" form. It was removed: it duplicated what the nine presets already
covered, added six dropdowns and a filter builder for no real gain over clicking
a preset, and its framing implied the presets above it needed an LLM when they
never did. The API still accepts an explicit `plan` (`POST /api/workbench/analyses`
with `plan` instead of `question`) — the presets use exactly that path — so nothing
in the backend changed, only the redundant manual UI. A tenth preset, "Outcome mix
by resource," was added in its place: it answers the same "do certain resources'
cases skew toward a particular outcome" question the manual builder could
construct, with an explicit caveat that this reflects case assignment, not
resource performance.

#### Reproduce the full-log case facts

```powershell
./.venv/Scripts/python.exe scripts/extract_outcomes.py `
  raw_data/bpi2017/BPI_Challenge_2017.xes raw_data/BPIC_2017_W.outcomes.csv
```

The streaming extractor also accepts `.xes.gz` and writes a companion `.json`
with source/output hashes and metric definitions. It emits 31,509 full-log cases;
the analyst joins by case ID to the 31,500 cases in the selected work-item CSV.
All 31,500 currently match. Extraction is separate from model learning and does
not alter the simulator's event log or trained snapshots.

- **Outcome:** last observed `A_Pending`, `A_Denied` or `A_Cancelled`, otherwise
  Unresolved. Pending does not establish that a loan was disbursed.
- **Cycle days:** first-to-last recorded event in the full case. These durations
  differ from the older work-item-only overview; neither proves business completion.
- **Rework count:** entries into `A_Incomplete`, not work-item resumes.
- **Offers:** distinct offer IDs at creation, with creation-event fallback for absent IDs.
- **Response hours:** sent-to-returned intervals matched by offer ID and unioned
  within a case so overlapping offers count once. Unreturned/missing intervals do
  not become zero. This is an elapsed-response proxy, not a measured causal label.

The rework view includes an explicit counterexample to “more rework always means
slower”: in this extraction the no-rework group's median full-case duration is
30.4 days, versus 14.9 days for one incomplete-state entry. Compare rework **within
outcomes** before interpreting this: long-lived cancelled cases can have no rework,
and case difficulty can affect both rework and timing. Both aggregate and conditional
questions are available as local presets.

Tests in `tests/test_analyst.py` cover extraction, overlapping response intervals,
missing-response denominators, filtering, conditional shares, source invalidation,
planner boundaries, persistence and unsupported predictions. The local browser
fixture checks charts, follow-up filtering, saved-result reload and mobile layout.

#### The overview charts also use the full log now

The Data Analyst above and the historical-overview charts (idle time, common paths,
hour-of-week heatmap, case duration by month, handover matrix) used to read different
data: the analyst joined the full log's case-level facts, but the overview charts were
still built from the `W_` work-item CSV alone, understating the true process — a case's
last recorded *work item* is often not when the case actually closed.

```powershell
./.venv/Scripts/python.exe scripts/extract_full_log.py `
  raw_data/bpi2017/BPI_Challenge_2017.xes raw_data/BPIC_2017_W.full_log.csv
```

This streams the whole log — application (`A_*`), offer (`O_*`) and work-item (`W_*`)
events — into a flat `case_id, activity, resource, start, end` CSV plus a provenance
`.json`, the same pattern as `extract_outcomes.py`. Work items keep real durations from
their start/complete lifecycle; every other activity is a single `complete` event with
no matching start and is recorded as instantaneous (`start == end`), never fabricated.

When this file exists, learning BPI 2017 again (model version 4) picks it up
automatically and uses it **only** for the overview charts — `webapp/workbench/model.py`'s
`discover()` takes it as an optional `full_events` argument that never touches resource
profiles, calendars, templates or arrival days; those keep coming from the `W_`-only
frame, so the simulator's resource-capacity model is byte-for-byte unaffected by whether
a full log is available. `explore()`'s `full_log` flag tells the UI which data it's
looking at, and its wording ("tasks" vs "events") follows accordingly.

This is what corrected the headline cycle-time number: with the full log, median case
duration is **19.1 days** (mean 21.9, p90 35.1), not the work-item-only 9.7 days — the
"idle time before each event" chart now also shows the gap after the last work item and
before the case's actual close (an automated step, or simply an unresponsive customer),
which the work-item-only view couldn't see at all. Activity count rises from 8 to 26,
touch-time share falls from 0.9% to 0.5%, and case count rises slightly (31,500 → 31,509)
because a handful of applications have no work item at all yet still appear in the full
log. Tests: `tests/test_full_log.py` (pairing logic, instantaneous non-`W_` events, a
stray work-item `complete` with no matching `start` is skipped, not fabricated) and
`tests/test_workbench.py::test_full_events_only_change_the_explorer_not_the_simulation_model`
(resource profiles/templates/arrival days are identical with or without a full log).

| Metric | Definition |
| --- | --- |
| Mean / median / p90 case duration | Elapsed hours from arrival to final task completion, including cases finishing after the horizon |
| Mean queue & calendar wait | Sum of resource queue and off-hours waits per case, averaged over arrived cases; excludes residual delay |
| Mean residual delay | Sum of estimated external/residual delays per case |
| Throughput | Cases completed within the arrival horizon divided by horizon days |
| Backlog | Arrived cases unfinished at the horizon boundary |
| Deadline compliance | Percentage of all arrived cases whose eventual elapsed duration is within the configured deadline |
| Utilization | Occupied working seconds within the horizon divided by scheduled working seconds within the horizon |

Each repetition uses `seed + repetition_index`. Means, ranges and population
standard deviations are reported, along with paired scenario-minus-baseline
differences. Ranges are **not confidence intervals**. A scenario with no changes
must exactly reproduce the baseline for the same seed.

The result shows simulated baseline duration beside the held-out historical
duration. They use different sampled arrival horizons, so this is a descriptive
fidelity check, not a calibrated forecast or the paper's formal evaluation.
Substantial discrepancies should be resolved before using results for staffing
decisions. Scenario outcomes are conditional on the model, especially its
calendar, serial-work and residual-delay assumptions.

## Saved artifacts

```text
runs/workbench/
  models/<model_id>.json             # immutable learned model, source hash, version
  jobs/<job_id>.json                 # durable progress / outcome
  experiments/<job_id>/
    request.json                    # exact scenario and simulation settings
    result.json                     # paired metrics, per-run details, assumptions
    baseline_1.csv ...
    scenario_1.csv ...
```

CSV logs include case arrival, activity readiness, start/end, assigned resource,
processing seconds, queue seconds and residual-delay seconds. No scenario deletes
or overwrites another experiment. Source logs and pre-existing research outputs
remain intact. Interrupted jobs are marked as errors on server restart; they can
be submitted again. The web UI reconnects to running jobs on refresh.

The workbench caps requests at 90 arrival days, 10 repetitions, 50 added resources,
30,000 cases / 300,000 events per repetition, and 15 minutes per comparison. One
background job runs at a time. Unsupported edits and removal of all resources
capable of an activity are rejected before execution.

## API

- `GET /api/workbench/datasets`
- `POST /api/workbench/models` with `{"dataset":"BPIC_2017_W"}`
- `GET /api/workbench/models/{model_id}` (public profiles; excludes raw samples/templates)
- `POST /api/workbench/experiments` (`ExperimentRequest`, see `/docs`)
- `GET /api/workbench/jobs` and `GET /api/workbench/jobs/{job_id}`
- `GET /api/workbench/experiments/{job_id}`
- `GET /api/workbench/experiments/{job_id}/files/{filename}`

## Checks

```powershell
./.venv/Scripts/python.exe -m pytest -q
node --check webapp/static/workbench.js
# With the app running, and Microsoft Edge installed:
./.venv/Scripts/python.exe scripts/verify_workbench_ui.py
```

Tests cover known queue outcomes, resource copying and replacement, calendars,
paired determinism, capacity conflicts, demand/backlog, train/test isolation,
empirical-tail frequency, invalid edits, isolated artifacts, and API behavior.
The browser check exercises actual BPI 2017 profiles, cloning, comparison,
CSV download, draft restoration and mobile layout. Screenshots are saved beneath
`runs/ui-checks/`. Windows execution restrictions may require running that browser
check outside the restricted shell sandbox.

## A bug caught by sanity-checking against the data

The first model version (`empirical-trace-v1`) estimated residual delay from the *last*
resource completion before a task's observed start. That silently converted customer
waiting into resource contention. On BPI 2017 the first baseline showed:

| | v1 (wrong) | v2 (fixed) |
| --- | --- | --- |
| Mean queue & calendar wait per case | **405 h** | 71 h |
| Mean residual delay per case | **6.9 h** | 263 h |
| Median simulated cycle time | 37 h | 239 h (held-out history: 219 h) |

Two things exposed it: the simulated median cycle time (37 h) was nowhere near the
held-out historical median (219 h), and the "add capacity" scenario should have helped
far more than it did if queues really were 405 h. v2 takes the *first feasible working
window after the case was ready*, so idle time is no longer mistaken for a busy resource
(`test_delay_uses_first_idle_window_not_the_last_completion` locks this in, and the
model version was bumped so v1 snapshots are refused).

This also changes the business conclusion. Under v1, capacity looked like the bottleneck;
under v2 about 78% of simulated cycle time is external delay, and adding or removing
individual people barely moves cycle time (see `PROJECT_GUIDE.md`, sections 4 and 6).

## How well does the baseline match history? (and why it was ~20% off)

Every result page ends with a validation panel: the simulated baseline against real held-out
cases. On BPI 2017 it first looked like a 20% overshoot (mean case duration 337 h simulated vs
281 h real). Two separate things were going on. Both can be reproduced with the scripts below.

### 1. The real number was measured unfairly

Held-out cases start after 19 Oct 2016, but the log stops on 1 Feb 2017. Cases arriving late have
less time to finish, so they are cut short and look faster than they were. The simulator runs
every case to completion. The real data shows it:

| Arrival week of the held-out case | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Follow-up left before the log ends (days) | 98 | 91 | 84 | 77 | 70 | 63 | 56 | 49 | 42 | 35 | 29 |
| Real mean case duration (h) | 311 | 317 | 310 | 302 | 296 | 266 | 265 | 254 | 258 | 208 | 238 |

Only using cases that are "concluded" would make this worse, not better: the slow cases are the
ones still open, so dropping them removes exactly the long durations. The log also has no
"finished" flag. Instead the app keeps held-out cases that **had enough time to finish**: at
least as much follow-up as 99.5% of the training cases needed (62 days on BPI 2017, 3,937 of
6,300 cases). If too little history remains, it falls back to all held-out cases and says so.

The reference depends somewhat on how strict that rule is (real mean / median / p90 in hours):

| Rule | Cases | Mean | Median | p90 |
| --- | --- | --- | --- | --- |
| All held-out cases | 6,300 | 281 | 219 | 602 |
| Follow-up >= 99th percentile (52 d) | 4,748 | 296 | 232 | 627 |
| **Follow-up >= 99.5th percentile (62 d), used by the app** | 3,937 | 300 | 232 | 626 |
| Follow-up >= 75 days | 2,817 | 313 | 238 | 646 |
| Follow-up >= 99.9th percentile (94 d) | 1,032 | 318 | 242 | 667 |

Applying the same cut-off to the simulated cases lowers the simulated mean by only ~11 h (3%),
while the real mean falls ~32 h (10%). So the log ending explains a good part of the gap but not
the whole drop: real durations also fall for cases arriving in weeks 5 to 8, where almost every
case had time to finish. With one year of data a seasonal or process change in Nov-Dec 2016
cannot be separated from other causes.

(`python scripts/check_baseline_censoring.py`)

### 2. Against the fair reference the baseline is +12% (mean), +3% (median), +18% (p90)

The median is right. The mean and the tail are too slow. The validation panel shows where:

| Measure | Real | Simulated | Difference |
| --- | --- | --- | --- |
| Mean case duration (h) | 300 | 337 | +12% |
| Median case duration (h) | 232 | 239 | +3% |
| 90th percentile duration (h) | 626 | 736 | +18% |
| Cases arriving per day | 80.8 | 84.2 | +4% |
| Tasks per case | 7.6 | 8.1 | +6% |

The last two rows are process drift: the model learns from Jan-Oct 2016, when cases had 8.1 tasks
and slightly more arrived per day than in Oct-Jan. That is not a simulator flaw.

Idle time before a task (time since the case's previous task ended) is right on average for most
activities (within ~20%) but wrong at the median for two: Validate application (31 h real, 20 h
simulated) and Call after offers (4 h real, 20 h simulated).

### 3. Root cause of the tail: a few extreme recorded durations

The panel also lists who holds the simulated queues: three people hold ~48% of all queueing
(User_121 alone 25%) even though their observed load in history is only 18-47%. Their typical
task is short (User_121: 2 minutes) but 79% of their total recorded working time sits in the
slowest 1% of tasks, including one 161-hour item. Across the log, **195 of 195,564 recorded tasks
(0.1%) hold 38% of all working time; the longest is 531 working hours.** These are almost
certainly work items left open and resumed weeks later, not real work. When the simulation
samples one, that person is blocked for weeks and a queue builds behind them.

Capping durations at 24 working hours (187 samples affected), in memory only:

| | Mean | Median | p90 | Top-3 share of queueing | Mean queue wait |
| --- | --- | --- | --- | --- | --- |
| As learned | +12.2% | +3.2% | +17.6% | 48% | 71 h |
| Capped at 24 h | -2.7% | -4.7% | -6.2% | 14% | 28 h |
| Capped at 8 h | -4.2% | -6.5% | -7.2% | 14% | 24 h |

So most of the remaining overshoot comes from 0.1% of the data, not from the method.

(`python scripts/diagnose_baseline_gap.py`)

### What is not yet done

- **The cap is not applied to the model.** It slightly over-corrects (a small undershoot),
  because the elapsed time of those weeks disappears. The better fix is to cap the working time
  and treat the excess as idle waiting (residual delay) rather than deleting it. This changes every
  headline number, so it should be a deliberate model-version change.
- **Sticky routing still matters.** Historical routing is too rigid (a person's queue never spills
  over), pooled routing is too flexible (it undershoots the real 300 h). Reality sits between them.
- **The Nov-Dec decline** in real case durations is unexplained.

## Bugs found and fixed after using the product like a user

Reading through the app rather than just the code surfaced three real bugs:

1. **"Learn resource profiles" always jumped back to Resource profiles.** Clicking it from the
   Data explorer (or any tab) silently discarded whatever you were looking at and navigated to
   Resource profiles once the model finished loading — the Data explorer looked broken (it never
   showed anything) because you were never actually left on it. Fixed by remembering which tab the
   learn was launched from and returning there — but only if you're still on it: if a background
   learn finishes while you've since moved to a different tab (e.g. you switched to the assistant
   mid-conversation), it now updates quietly instead of pulling you away (`workbench.js`,
   `state.pendingView`).
2. **The chat page looked and lived like a different product.** It was a separate HTML document
   with its own dark theme, unrelated to Process Lab's design, reachable only via an external link.
   It is now the **Simulation assistant** tab inside the same page — same sidebar, same cards,
   same colours. `/chat` still resolves, as a redirect (`webapp/main.py`, `webapp/static/index.html`
   removed).
3. **The assistant never answered.** A message just sat at "processing" and then failed with
   "Connection error." The DeepSeek key itself was fine (confirmed by calling it directly from a
   clean shell); the already-running server process had inherited an unusable loopback proxy
   from its launch environment. During the later reliability check this was identified as
   the restricted execution environment's proxy at `127.0.0.1:9`.
   Restarting the server picked up a clean environment and fixed it — not a code bug, but worth
   documenting because "the assistant is silently down" has no other visible symptom. If this
   happens again: check `runs/workbench-server.err.log` for `ProxyError`/connection failures around
   `openai._base_client`, and restart the server.

LoanApp was also removed from every place a user sees it (Process Lab's dataset list, the
assistant's dataset list, the docs) — it's a synthetic log that doesn't belong in a product about
real data. It stays registered internally (`user_facing=False` in `dataset_registry.py`) because
the test suite depends on it for a fast (~2 min), deterministic end-to-end run; BPI 2017 is too
large for that.
