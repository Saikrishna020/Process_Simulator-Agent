# Process Lab: history-based resource experiments

Process Lab learns resource profiles from an event log and compares a historical
baseline with a changed resource setup. It runs locally, without an LLM API key.
The existing research simulator and DeepSeek chat remain available at `/chat`.

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

LoanApp is also supported. The current BPI 2019 CSV has zero-duration events and
is disabled in the selector because it cannot teach resource processing capacity.
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
