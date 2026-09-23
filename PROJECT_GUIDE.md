# Process Lab / AgentSimulator — the end-to-end guide

*For someone who has never seen this project and may know nothing about process mining.*
*Every number below was computed from the files in this repo (see §12 for how to reproduce).*

---

## 0. The 60-second version

**Companies leave a trail.** Every time a loan application is validated, a customer is called, or a purchase order is created, some system writes a row: *which case, what happened, who did it, when it started, when it ended.* That table is an **event log**. **Process mining** is the discipline of turning event logs into an understanding of how work *really* flows — as opposed to how the flowchart on the wall says it does.

**This project does two things with event logs:**

1. **Learns a working model of the organisation from history** — who does which tasks, how long they take, when each person works, who hands work to whom, how fast cases arrive.
2. **Lets you ask "what if?"** — add two people, remove your busiest person, halve the customer-response delay, get 50 % more applications — and *simulate* the alternative future next to a historical baseline, with paired random numbers so the difference you see is caused by your change and not by luck.

**Three layers exist in the repo** (§5):

| Layer | What it is | Needs an LLM? |
|---|---|---|
| **Research engine** (`source/`) | A multi-agent simulator from the *AgentSimulator* paper (Kirchdorfer et al., 2024). Each employee is a software agent. Scored against held-out real data. | No |
| **Process Lab workbench** (`webapp/workbench/`, served at `/`) | *Your* addition: a fast, inspectable "what-if" tool with a browser UI. | No |
| **Simulation assistant** (`webapp/orchestrator/`, the "Simulation assistant" sidebar tab) | A LangGraph + DeepSeek agent that turns "simulate BPIC_2017_W with 3 runs" into a validated, human-approved run of the research engine. Same page and design as the other two — one product, not a bolted-on chat window. | Yes (DeepSeek key) |

**The five things the data actually says** (§3, §4):

1. In BPI 2017, a typical case takes **19.1 days** door to door (using only the human work-item subset understates this at 9.7 days) but only **~25 minutes of hands-on work**. Hands-on work is **0.5 %** of elapsed time. The rest is waiting.
2. Most of that waiting is **not** caused by lack of staff: system utilisation in the model is ~6 %. It is caused by *external* delays (customer callbacks, documents arriving).
3. Consequently, **adding or removing people barely moves cycle time** (removing the single busiest person: −0.2 %; adding three copies of them: −0.2 %), while **halving the external delays** moves it by ~37 %.
4. The one lever inside the organisation that *does* matter is **how work is allocated**: sending each task to the *earliest available qualified person* instead of the historically preferred person cuts queue wait by 96 % (61 h → 2 h) and cycle time by ~18 %.
5. The research engine reproduces the paper's numbers on LoanApp, is comparable on BPI 2017 for control-flow, and is **~2.6–3× worse than the paper on the cycle-time metrics (REDD, CTDD)** for BPI 2017 (§6). That gap is a finding to be honest about, not hide.

---

## 1. Process mining in ten minutes (what you must know)

### 1.1 The event log — the only input

| Column | Meaning | Example (BPI 2017) |
|---|---|---|
| **case_id** | One instance of the process. A "case" is one loan application, one purchase order, one patient… | `Application_652823628` |
| **activity** | A step that was performed | `W_Call after offers` |
| **resource** | The person / system who did it | `User_52` |
| **start_timestamp** | When work began | `2016-01-02 11:30:28` |
| **end_timestamp** | When work ended | `2016-01-02 11:32:41` |

Vocabulary used everywhere in this repo:

- **Trace / variant** — the ordered list of activities of one case. Two cases with the same list are the same *variant*. Real processes have thousands of variants (BPI 2017: 6,787 variants across 31,500 cases).
- **Cycle time** — elapsed time from a case's first start to its last end.
- **Processing (touch) time** — time someone was actually working on it (`end − start`).
- **Waiting time** — everything else. Cycle time = processing + waiting.
- **Queue wait vs. extraneous delay** — waiting has two very different causes:
  *queue wait* = the task was ready but the resource was busy or off-shift (**fixable with capacity**);
  *extraneous / residual delay* = the task simply wasn't started yet for reasons the log can't see, e.g. waiting for a customer to send a document (**not fixable with capacity**). Distinguishing these is the crux of §4.
- **Resource calendar** — the hours a resource is available. The log doesn't contain rosters, so they are *inferred* from when the person has been observed working.
- **Concurrency / multitasking** — one resource with several open items at the same time. BPI 2017: 15.4 % of work items start while the same person still has another item open.

### 1.2 The three families of process mining

1. **Discovery** — learn the process (flow, roles, timing) from the log. *Both engines here discover a model of resources, timings and handoffs.*
2. **Conformance** — compare log vs. a reference model. *Not done here.*
3. **Enhancement / simulation** — use the discovered model to predict performance and test changes. **This is the project's core.**

### 1.3 Business Process Simulation (BPS) and why it is hard

Classic BPS draws a flowchart and decorates it with parameters (arrival rate, durations, resource pools). The AgentSimulator paper argues this is *control-flow-first* and misses that **individuals differ**: one clerk takes 45 min, another 15; someone works part-time; Angela always hands work to Patrick, never to Maria. It proposes **resource-first**: model each resource as an **agent** with its own schedule, skills, speed and handover habits, and let the process *emerge* from their interaction.

A simulation is only useful if you can trust the baseline. Hence the standard recipe, which this repo follows:

1. **Temporal split** — learn on the *first 80 %* of cases (by start time), test on the *last 20 %*. Cases straddling the cutoff are dropped. (Never split randomly: it leaks the future.)
2. **Simulate** the test period, generate a synthetic log, and **score** it against the held-out real log with distance metrics (§6.1). Lower = more realistic.
3. Only then run counterfactuals.

### 1.4 Concepts worth being able to explain in an interview

- **Why temporal, not random, split?** Processes drift; random splits let the model "see" the future.
- **Why paired random numbers (common random numbers)?** Baseline and scenario reuse the same arrivals and the same random draws per case, so their *difference* has far lower variance than two independent runs. A scenario with no changes reproduces the baseline **exactly** (there is a test for this).
- **Why "empirical" durations?** The workbench samples from the real observed working-time list instead of fitting a Gamma/Log-Normal — no distributional assumption, and rare long tasks stay represented.
- **Why subtract off-hours from durations?** A task that started Friday 16:00 and ended Monday 09:00 did not take 65 hours of work. Only time inside the person's calendar counts.
- **What is a "cool-down"/"warm-up" problem?** Starting the simulation with an empty system understates queues at the beginning; ending it at a fixed horizon leaves unfinished cases. Both matter for the *backlog* metric here (§8).

---

## 2. The datasets

Three logs are registered (`webapp/orchestrator/dataset_registry.py`). One file each in `raw_data/`.
Only BPIC_2017_W is offered in the product; LoanApp is kept registered but hidden (`user_facing=False`) as a fast, deterministic fixture the test suite runs an actual end-to-end simulation against — real data tells the better story here, and BPI 2017 is too large for a ~2-minute test.

| | **LoanApp** | **BPIC_2017_W** | **BPIC_2019** |
|---|---|---|---|
| Origin | Synthetic loan application process (from the paper's repo) | Real: Dutch financial institution, loan applications, 2016 (BPI Challenge 2017) | Real: purchase-order handling at a large multinational (BPI Challenge 2019) |
| Cases | 1,000 | 31,500 | 251,734 |
| Events | 7,492 | 254,382 | 1,595,923 |
| Activities | 12 | 8 (7 material) | 42 |
| Resources | 19 | 149 | 628 |
| Time span | 9 Feb – 9 May 2023 | 2 Jan 2016 – 1 Feb 2017 | 1948(!) – Apr 2020, mostly 2018–2019 |
| Has real durations? | **Yes** | **Yes** | **No — 100 % of events are zero-duration** |
| Works in Process Lab? | Yes | **Yes (the flagship)** | **No** (disabled in the UI, with reason) |
| Works in research engine? | Yes | Yes | Only pre-processed; no simulated logs were ever produced |

### 2.1 LoanApp (small, clean, synthetic)
A bank loan process: *Check application form completeness → Check credit history / AML check / Appraise property (any order) → Assess loan risk → (Design loan offer → Approve loan offer → Cancel application) or Reject application*, plus a loop *Return application back to applicant → Applicant completes form*.
- Outcomes: 416 rejected, 355 cancelled, 229 approved application (= 1,000).
- 44 variants; 108 cases go through the return-to-applicant loop.
- Resources: 8 Clerks, 2 Appraisers, 2 AML Investigators, 4 Loan Officers, 2 Senior Officers, 1 Applicant.
- Everyone works **Mon–Fri, ~07:00–15:00** only (clean synthetic calendar).
- Cases are fast: **median 2.6 h**, mean 10.3 h, p90 19.9 h, max 2.9 days.
- **Use:** a quick sanity dataset (research engine finishes in ~1–2 minutes) and a *reproduction check* against the paper.

### 2.2 BPI Challenge 2017 → `BPIC_2017_W` (real, the flagship)
The raw file `raw_data/bpi2017/BPI_Challenge_2017.xes` (578 MB) has **31,509 cases and 1,202,267 events**, in three families:

| Family | Events | Meaning |
|---|---|---|
| `A_…` | 239,595 | **A**pplication *states* (submitted, accepted, denied…) |
| `O_…` | 193,849 | **O**ffer *states* (created, sent, cancelled…) |
| **`W_…`** | 768,823 | **W**ork items: manual tasks that a human resource performs |

Only **W_** items describe *human work with durations*, which is what resource simulation needs, so `raw_data/xes_to_csv.py --w-only` keeps them. Crucially, XES stores each work item as **several lifecycle events** — `schedule` (149k), `start` (128k), `suspend` (215k), `resume` (127k), `complete` (475k), `ate_abort` (85k), `withdraw` (22k). The converter **pairs `start/resume → complete/suspend` into intervals**, ignoring `schedule/withdraw/ate_abort`, producing **254,382 intervals** with a real start and end. That is the flat 5-column CSV both engines consume.

The eight activities (interval counts): `W_Validate application` 72,937 · `W_Call incomplete files` 62,260 · `W_Call after offers` 60,560 · `W_Complete application` 52,321 · `W_Handle leads` 4,891 · `W_Assess potential fraud` 1,323 · `W_Shortened completion` 81 · `W_Personal Loan collection` 9.

> **Note:** the paper reports 30,276 traces / 240,854 events for "BPI17W". Our extraction has 31,500 / 254,382, i.e. a slightly different pairing/filtering. Results are therefore *close to* but not *identical to* the paper's benchmark (§6).

### 2.3 BPI Challenge 2019 (real, large, unusable for capacity — a lesson)
Purchase-order handling: `Create Purchase Order Item`, `Record Goods Receipt`, `Record Invoice Receipt`, `Clear Invoice`, `SRM: …` and 35 others. Problems, all verified:
- **Every event is zero-duration** (`start == end`) — the source XES has no start/complete lifecycle, so the converter treats each as instantaneous. Without durations, "how long does this person take" and "how busy are they" cannot be learned. The Process Lab refuses it with an explanation.
- **25 % of events (399,090) have resource `NONE`**; `user_002` alone did 10 %; many `batch_xx` are automated.
- Timestamps go back to **1948** — obvious data-entry anomalies.
- The research-engine runs on it failed (see `raw_data/bpi2019_run*.log`, incl. the Windows `mkdir` bug fixed later) and **no `simulated_log_*.csv` exists for it** — only the pre-processed train/test split.
- **Lesson worth stating in a portfolio:** *fit-for-purpose data check comes before modelling.* A simulator that can't tell a log has no durations would silently produce nonsense.

---

## 3. What the BPI 2017 event data says (the actual exploration)

All computed directly from `raw_data/BPIC_2017_W.csv`.

### 3.1 The process is mostly a "call the customer" loop
- Typical case: **7 work items** (mean 8.1, max 65).
- Most common start: `W_Complete application` (83 %); other starts: `W_Handle leads` (11.5 %), `W_Call after offers` (5.7 %).
- The single most common variant (**11.6 %** of cases): `Complete application → Call after offers → Call after offers`.
- **71.5 % of cases have exactly two "Call after offers"**; 95.5 % of cases repeat at least one activity. This is rework/chasing, not a straight line.
- Fragmented: 6,787 variants for 31,500 cases; the top 5 cover only 24 %; 16.5 % of cases are unique.
- Case endings: `Validate application` (49.6 %), `Call after offers` (30 %), `Call incomplete files` (19.5 %).

### 3.2 Time: it's almost all waiting
| | value |
|---|---|
| Cycle time, median | **9.7 days** |
| mean / p90 / max | 12.8 / 26.1 / 286 days |
| Hands-on (touch) time, median case | **25 minutes** |
| Touch time as share of total elapsed time | **0.9 %** |
| Typical single work item | 0.5–5 min (median 1.65 min) |
| Median gap between two consecutive work items in a case | **4.3 h**, but for `Validate application` it is **21.9 h** (mean 64 h) and for `Call after offers` **17.3 h** (mean 51 h) |

So a case is a ~25-minute job that takes ~10 days by the work-item subset alone. **The improvement opportunity is in the gaps, not in the tasks.** Monthly median cycle time is remarkably flat (8.2–10.9 days), so the process is stable over the year — a good sign for training on the past to predict the future.

**This table understates the true cycle time.** It's built from `W_` work items only — the last recorded event of a case is often the last thing a *human* did, not when the case actually closed. Data Explorer now also loads the full log (`A_`/`O_`/`W_` events, via `scripts/extract_full_log.py`) and the true median jumps to **19.1 days** (mean 21.9, p90 35.1) — nearly double. Touch-time share drops from 0.9% to 0.5%: waiting is even more dominant than this table shows. The gap is time between the last work item and the case's formal close (an automated step, or the customer simply not responding) — itself a finding, not an artefact. §6 and `WORKBENCH.md` have the detail; the simulator's own model (§5, §6) is unaffected, since it correctly uses only `W_` events for resource capacity regardless.

### 3.3 People: many generalists, moderate concentration, calendar of a real office
- **149 resources.** Top 10 do 24 % of events, top 20 do 39 %, the bottom half of the workforce does only 13 %.
- A typical resource does **~5 of the 8 activity types**. Only 19 % are specialists (≥80 % of their work in one activity). Yet there *are* stars: `User_87` does 7,331 items (mostly `Validate application`), `User_5` 5,980 (mostly `Call after offers`).
- Handoffs are sticky: 26.8 % of the time the next item goes to the **same person**; `User_87` hands to themself 42 % of the time.
- Rhythm: 93 % of work is Mon–Fri, Saturday 6.6 %, Sunday 0.2 %. 89 % of items start between 07:00 and 17:59 **UTC** (Dutch local time is +1/+2 h — see §9).
- 15.4 % of work items overlap with another item of the same person (multitasking) — a direct violation of the "one task at a time" assumption of most simulators.

### 3.4 Data quality notes
- 0.16 % of work items last more than a day (max 136 days — pairing artefacts); `W_Personal Loan collection` (9 items) has a median of ~20 days.
- `W_Shortened completion ` has a **trailing space** in its name in the data.
- Resource names are anonymised (`User_n`).

---

## 4. The insight that shapes everything: capacity is not the bottleneck

Combine §3.2 and §3.3: hands-on time is 0.9 % of elapsed time and the average simulated utilisation is ~6 %. The Process Lab's discovery step classifies each historical wait as either *queue wait* (the resource was busy / off-shift) or *residual delay* (the resource was free but nothing happened — the customer, an approval, a system…). On BPI 2017 v2 of the model:

- **Mean residual delay per case: 263 h**, **mean queue+calendar wait: 71 h**, in a **337 h** simulated mean cycle.
- i.e. **~78 % of cycle time is external delay; ~21 % is queueing; processing is ~1 %.**

That is why the what-if results in §7 look the way they do. It also matches the research engine: with `extraneous delays` switched *on* the cycle-time distance on the validation window is **~90 vs. ~298** with them *off* — a 3× improvement — and the auto-selection chose "delays on".

---

## 5. What is in the repo — architecture

```
AgentSimulator/
├── raw_data/            source logs (LoanApp.csv.gz tracked; BPIC files are big & untracked)
│   └── xes_to_csv.py    streaming XES → CSV converter (start/complete pairing, --w-only)
├── source/              RESEARCH ENGINE (paper code; Mesa multi-agent simulation)
├── simulate.py          CLI entry to the research engine
├── evaluate_run.py      the 5 paper metrics vs. held-out test log
├── webapp/
│   ├── main.py          FastAPI app: "/" = Process Lab, "/chat" = orchestrator
│   ├── workbench/       PROCESS LAB backend (model.py, simulation.py, service.py, api.py, schemas.py, calendar.py)
│   ├── orchestrator/    CHAT AGENT (LangGraph graph, guardrails, tools, jobs)
│   └── static/          workbench.html/css/js — one app: Process Lab + the Simulation assistant tab
├── runs/                run manifests, LangGraph checkpoints, workbench/{models,jobs,experiments}
├── simulated_data/      research engine outputs (train/test split, simulated logs)
├── tests/               28 tests
├── README.md  WORKBENCH.md  ORCHESTRATOR.md   (+ this guide)
```

### 5.1 Layer 1 — the research engine (`source/`, `simulate.py`)

*Credit: the simulator core is from the AgentSimulator paper's public code; this project extended and hardened it.*

Pipeline (`AgentSimulator.execute_pipeline` → `discover_simulation_parameters` → `simulate_process`):

1. **Split** — sort events by end time; first 80 % train, last 20 % test; drop cases straddling the boundary. The **last 20 % of the training cases** is kept as a *validation* set.
2. **Preprocess** — events with no resource get a synthetic agent `artificial_<activity>` (they're usually system/instant tasks); every resource becomes an integer agent; a synthetic end event `zzz_end` is appended to each case so "the case ends" becomes a learnable transition.
3. **Discover per-agent properties**
   - *Roles* — resource pools by correlating activity-frequency profiles (Pearson, threshold 0.7) and grouping in a graph.
   - *Calendars* — one weekly availability schedule per agent.
   - *Processing times* — per (agent, activity): working-time-only durations, outliers filtered, then the best-fitting distribution (Exponential, Gamma, Normal, Uniform, Log-Normal or fixed) by Wasserstein distance.
   - *Capabilities* — the activities each agent has ever performed.
4. **Discover behaviour**
   - *Orchestrated* handover: next activity chosen globally from the case's activity *prefix* (falls back to shorter suffixes if unseen).
   - *Autonomous* handover: the **current agent** decides the next activity and **who** gets it, from per-agent activity and handoff probabilities.
   - *Prerequisites*: which activities must precede which.
5. **Arrivals** — best-fit inter-arrival distribution + per-weekday earliest/latest arrival time and mean cases/day.
6. **Extraneous delays** — per-activity "timer" distributions for waiting that isn't resource contention (from Chapela-Campa & Dumas).
7. **Automatic configuration** — simulates the validation window under **4 configurations** (orchestrated/autonomous × delays on/off) and picks the one with the lowest cycle-time distance.
8. **Simulate** with Mesa: a *ContractorAgent* (the case dispatcher) asks candidate `ResourceAgent`s in order of specialism → availability (→ handover probability in autonomous mode); an agent accepts only if it is free and the task fits its calendar, else the case waits; extraneous delay is added first; multitasking is allowed for activities that historically never wait.
9. **Write** `simulated_log_0..N.csv` and evaluate (§6.1).

### 5.2 Layer 2 — Process Lab, the workbench (`webapp/workbench/`)

A **separate, deliberately simpler engine** built for interactive what-ifs and inspectability ("empirical trace-resampling", `engine = empirical-trace-v3`). It exists because the research engine takes minutes-to-hours and needs a temporal setup per run; the Lab learns once (~50 s on BPI 2017), caches an immutable model, and runs comparisons in **~10 s**.

**Learning (`model.py: discover`)**
1. Validate the log (no missing IDs, no negative durations; refuses an all-zero-duration log; needs ≥10 cases).
2. **Temporal split** at the 80th-percentile case start (BPI 2017: cutoff **19 Oct 2016**). Training cases must *finish before* the cutoff (24,041); test cases must *start after* it (6,300); 1,159 straddlers are excluded. **Only training data feeds the model** (a test proves it).
3. **Per resource (137 profiles):** inferred calendar (earliest → latest observed hour per weekday, UTC), each activity's *empirical working-time samples* (off-calendar time removed), median/mean/p90, observed load %, top-5 handoffs.
4. **Trace templates:** every training case's sequence `[activity, preferred resource, residual delay]` is frozen. Residual delay = time from the *first feasible working window after the case was ready* to the *observed start* — v2 fixes v1's bug (§9).
5. **Arrivals:** for each weekday, the real list of per-day arrival-time offsets; simulation picks a historical day and reuses its arrivals.
6. Stores assumptions, overlap %, and a fair held-out reference (cases with enough follow-up: mean 300 h, median 232 h, p90 626 h; all held-out cases would read 281 / 219 / 602 h because the log ends early), per-activity idle-time statistics, and the descriptive statistics behind the Data explorer.

**Simulating (`simulation.py: simulate`)**
- A **ready-time priority queue**: pop the earliest ready task, choose a resource, work it inside the calendar (pausing at shift end / weekends), release the resource, schedule the next task at `end + residual delay`.
- **Allocation rule** — `historical`: give it to the template's preferred resource (or a *copy* of it), else any qualified peer; `pooled`: always the earliest-available qualified resource.
- **Duration** — a seeded quantile lookup in that resource's empirical samples × resource multiplier × activity multiplier.
- **Paired design** — arrivals, templates and per-task quantiles are derived from `seed` and case index, so baseline vs. scenario share draws. Each repetition uses `seed + i`.
- **Scenario levers** (`schemas.py`, strictly validated by Pydantic): *clone* a resource (×1–20, with a custom schedule or speed), *remove* one (rejected if it strands an activity), change a resource's speed or working hours, scale an activity's processing time or its residual delay, scale demand (25–300 %), pick the horizon (7–90 days), repetitions (1–10), deadline, seed, routing.
- **Guardrails:** ≤50 added resources, ≤30,000 cases / 300,000 events per repetition, 15-minute budget, one background job at a time, backlog >5 years rejected.

**Reported metrics** (paired mean / range / paired difference): arrived cases · completed in horizon · backlog · throughput/day · mean/median/p90 cycle time · mean queue+calendar wait · mean residual delay · deadline compliance · utilisation · active resources · per-activity wait · per-resource utilisation. Ranges are **not** confidence intervals (the UI says so).

**Reproducibility & persistence** — content-hashed model ids, per-job JSON, per-experiment `request.json` + `result.json` + one CSV event log per repetition and side; interrupted jobs are marked as errors on restart; nothing overwrites anything.

**The UI (`/`)** — a dark-themed, single-page app (no framework):
- **Resource profiles:** searchable table + detail panel (activities, median times, weekly calendar, top handoffs), "add to scenario".
- **Scenario builder:** the settings above plus a live "your changes" list and an activity editor.
- **Data explorer:** the log itself, before any simulation: work vs. waiting, idle time per task, most common paths, hour-of-week heatmap, case duration by month, handover matrix.
- **Results:** before → after cards with colour-coded deltas, a "where cases wait" bar chart per activity, per-resource utilisation table, a *validation panel* (simulated baseline vs. held-out cases that had time to finish, per-activity idle time, who holds the queues), all-metrics table, JSON/CSV download.
- **Experiment history:** every job with status, and "reuse these settings".
- Progress bar with reconnect, draft changes saved in `localStorage`, mobile layout, and a "Model assumptions & data quality" panel.

**API** (`/api/workbench/*`): `datasets`, `models` (POST to learn, GET a public profile), `experiments` (POST/GET), `jobs`, and per-experiment file downloads. OpenAPI docs at `/docs`.

### 5.3 Layer 3 — the chat orchestrator (`webapp/orchestrator/`, `/chat`)

Natural language → safe execution of the research engine.

```
understand_request (LLM) → validate (code) → confirm_run (human interrupt) → run_simulation (subprocess)
                                                                                    → evaluate_results → summarize
```
- The LLM can only emit two typed objects: `SimulationRequest` or `ClarificationNeeded`. No tool runs code or shell.
- **Guardrails** (`guardrails.py`, `ORCHESTRATOR.md`): secret redaction in logs · path containment inside `raw_data/` · schema validation of columns · **mandatory human confirmation** (LangGraph `interrupt`) · `subprocess` with an argument list (never a shell) · bounded retries and a timeout · concurrency limit of 1 · `num_simulations` cap · a JSON manifest per run · localhost-only binding.
- State is checkpointed to SQLite so a session survives restarts; LangSmith tracing is optional.
- Every LLM proposal is **re-validated right before execution**, not just once.

### 5.4 Engineering work beyond the paper's code
- Fixed a Windows `mkdir` shell-out that silently killed every retried BPIC_2019 run.
- Fixed `ProcessPoolExecutor` crashes (`BrokenProcessPool` / denied pipe creation) in enabled-time discovery with a sequential fallback.
- Each run now writes to an isolated `simulated_data/<dataset>/<mode>/<run_id>/` directory, so results are never overwritten.
- Wrote the streaming XES→CSV converter (handles 578 MB with constant memory).
- **28 tests:** guardrails (8), tools/end-to-end LoanApp (3), LangGraph routing with a scripted fake LLM (4), Process Lab (13: calendar arithmetic, empirical-tail frequency, delay estimation, exact-baseline determinism, resource cloning/removal, demand/backlog, train/test isolation, schema validation, background jobs, artifact traversal, API without an LLM, Windows fallback). A separate Edge-driven UI check script exists. (I read these from the repo and `runs/workbench/verification.json`; I did not re-run the suite while writing this guide.)

---

## 6. Results so far

### 6.1 The research engine, scored (I re-ran `evaluate_run.py` on the saved logs — 10 simulations each)

Five distance metrics, all "lower is better", against the held-out test log:

| Code | Name | Answers |
|---|---|---|
| **NGD** | N-gram distance (n = 3) | Are the *sequences* of activities realistic? (control-flow) |
| **AEDD** | Absolute event distribution | Do events land at the right *calendar hours*? |
| **CEDD** | Circadian event distribution | Right hours of the day/week? |
| **REDD** | Relative event distribution | Right timing *relative to each case's start*? |
| **CTDD** | Cycle-time distribution | Right *case durations*? (includes congestion) |

| Log | | NGD | AEDD | CEDD | REDD | CTDD |
|---|---|---|---|---|---|---|
| **LoanApp** | this repo | 0.080 | 2.97 | 0.221 | 1.38 | 1.60 |
| | paper | 0.07 | 2.78 | 0.21 | 1.34 | 1.49 |
| **BPIC_2017_W** | this repo | 0.225 | 234.6 | 2.37 | 66.9 | 69.8 |
| | paper (BPI17W) | 0.30 | 221 | 1.64 | 26.0 | 22.8 |

**How to read it honestly**
- LoanApp **reproduces the paper** within run-to-run noise → the pipeline and the evaluation are correctly wired.
- On BPI 2017 our control-flow (NGD) is *better* than the paper's and absolute timing (AEDD) is comparable, but **cycle-time-related metrics (REDD, CTDD) are ~3× worse**. The datasets differ (our 31,500-case extraction vs. the paper's 30,276; our split 24,349/6,010 cases), so this is *not* a like-for-like failure — but it is a real signal that the cycle-time fidelity on BPI 2017 is not yet at paper level. Candidate causes (not yet tested): the different pairing/filtering of lifecycle events, the long-duration outliers (§3.4), and the delay-discovery step.
- The automatic configuration is **not stable across runs on LoanApp** (four recorded runs picked three different winners, with winning CTD values of 2.5–3.5 and the four candidates often within ~1 point of each other — so the choice is noisy). On BPI 2017 the choice was clear: *delays on, autonomous handover* (val CTD 85.7 vs. 298 without delays).

### 6.2 The Process Lab experiments on record (BPI 2017, model v2, 30 days, 3 repetitions, seed 42)

| Run | Baseline | Scenario | Change |
|---|---|---|---|
| Unchanged control (7 days) | identical | identical | **0.0 % on every metric** — determinism verified |
| +2 copies of `User_1` | mean cycle 337.0 h | 336.8 h | −0.05 % · `User_1` utilisation 49 % → 16 % · deadline met 22.37 % → 22.41 % |
| +3 copies of the busiest resource, `User_87` | mean cycle 337.0 h | 336.4 h | −0.19 % · queue wait −0.9 % · deadline met 22.37 % → 22.39 % |

**Sanity check of the baseline vs. history** (the Lab's validation panel): simulated mean/median/p90 cycle time = 337 / 239 / 736 h. Against *all* held-out cases (281 / 219 / 602 h) that looks like +20 % / +9 % / +22 %, but that reference is unfair: the real log stops on 1 Feb 2017, so late-arriving cases are cut short. Against held-out cases that had enough time to finish (300 / 232 / 626 h, 3,937 cases) the baseline is **+12 % / +3 % / +18 %**. The median is right; the mean and tail are inflated mainly by **0.1 % of recorded task durations** (195 of 195,564 hold 38 % of all working time, up to 531 hours; probably work items left open). Capping them at 24 working hours turns the gap into -3 % / -5 % / -6 % and cuts the top-3 queue share from 48 % to 14 %. Details and reproducible scripts: `WORKBENCH.md` ("How well does the baseline match history?"), `scripts/check_baseline_censoring.py`, `scripts/diagnose_baseline_gap.py`.

### 6.3 What-if runs I made for this guide (ad-hoc, **not saved** to `runs/`; 30 days, 2 repetitions)

Baseline for these: mean cycle 327 h, mean queue wait 61.5 h, mean residual delay 262.8 h.

| Scenario | Mean cycle | Queue wait | Deadline (120 h) met | Reading |
|---|---|---|---|---|
| Remove `User_87` (the busiest person) | −0.2 % | −1.7 % | 22.6 → 22.6 % | **No effect** — others absorb the work |
| +3 copies of `User_87` | −0.2 % | −1.2 % | 22.6 → 22.7 % | **No effect** — capacity isn't scarce |
| Halve `Validate application` *processing time* | −5.4 % | −27.6 % | 22.6 → 23.1 % | Small: tasks are only minutes long |
| **Halve `Validate application` residual delay** | **−15.9 %** | +6.9 % | 22.6 → 25.2 % | The wait *between* steps matters |
| **Halve `Call after offers` residual delay** | **−14.7 %** | +2.0 % | 22.6 → 35.8 % | Customer-response time is the lever |
| **Halve all residual delays** | **−37.0 %** | +17.1 % | 22.6 → 44.1 % | Biggest possible gain |
| **Pooled allocation** (earliest qualified) | **−17.6 %** | **−96.2 %** (61.5 → 2.4 h) | 22.6 → 27.5 % | Sticky handoffs create almost all queueing |
| Demand × 1.5 | +9.0 % | +47 % | 22.6 → 21.8 % | System has large slack |
| Demand × 3.0 | +38.2 % | +200 % | 22.6 → 20.1 % | Still absorbs 3× volume |

> **Why the "wait" rises when delays halve:** cases arrive at resources more compactly, so they queue slightly more; queue wait and residual delay are competing explanations of the same elapsed time.
>
> **Caution on pooled routing:** it assumes *anyone who has ever done an activity is qualified for every case* and samples that person's own durations. Real routing constraints (skills tiers, approval rights, geography) are invisible in the log. Treat "−96 %" as an upper bound on what better dispatching could achieve.

---

## 7. What the project demonstrates (the "so what")

1. **A full analytic pipeline:** raw XES → converter → cleaned event log → discovery → simulation → statistically-paired what-ifs → downloadable artefacts.
2. **A non-obvious, defensible business finding:** *in this process, hiring or firing barely matters; reducing external delay and improving allocation does.* Reached by modelling queueing and delay separately.
3. **Scientific hygiene:** temporal split, held-out validation, paired seeds, control run with zero difference, stated assumptions, honest fidelity gap.
4. **Software engineering:** typed schemas everywhere, immutable content-hashed models, a bounded single-worker job system with crash recovery, a REST API with OpenAPI docs, a dependency-free frontend, 28 tests.
5. **AI-safety engineering:** the LangGraph agent's ten guardrails and the "LLM proposes, code validates, human approves" pattern.

---

## 8. Known limitations (say these first — it makes the rest credible)

- **Not a forecast.** Against a fair reference the baseline is +12 % mean, +3 % median, +18 % p90 (the old +20 % included an unfair, cut-short reference). Most of the remaining overshoot comes from a few extreme recorded durations, not yet corrected in the model. Use it to compare scenarios, not to predict days.
- **One task at a time, no parallel branches, no batching, fatigue or learning** — although 15.4 % of real BPI 2017 items overlap. (The research engine allows multitasking only for activities that historically never wait.)
- **Calendars are inferred, in UTC.** "Earliest to latest observed hour per weekday" is not a roster; one Sunday evening event gives someone a Sunday window (e.g. `User_2`). Dutch local time is UTC+1/+2, so displayed hours are shifted.
- **Cloning a resource copies the source's behaviour.** A real new hire wouldn't behave identically.
- **Residual delay is a residual, not a cause.** It bundles customer delay, approvals, unobserved work, and modelling error.
- **Short horizons mislead the "backlog" metric.** The 7-day control run shows 533 of 620 cases as "backlog" simply because cases take ~12 days end to end and the simulation starts empty. Use ≥30-day horizons.
- **The default 120-hour deadline is arbitrary** (only ~22 % of historical cases meet it; median is ~219 h).
- **Utilisation is low (~6 %)** partly because inferred windows are wide and only `W_` items are modelled — it is *relative*, not an absolute staffing figure.
- **Process steps can't be added or removed**; only resources, times, delays, demand and routing can.
- **Local single-operator tool**: no auth, one worker.

---

## 9. Repo review: what was fixed and what is left

**Fixed (Sept 2026)**
1. The Process Lab (`webapp/workbench/`, UI, tests, `WORKBENCH.md`, scripts) was untracked; it is now committed.
2. `.gitignore` already excluded `/raw_data/`, `/simulated_data/` and `/runs/` (anchored with a leading `/`); an earlier draft of this guide wrongly said it did not. `.env` was never in git history.
3. The stale experiment and its v1 model were removed. The v1 bug is now documented in `WORKBENCH.md` ("A bug caught by sanity-checking against the data"). The mislabelled "busiest resource" run (it cloned the alphabetically-first `User_1`, 740 events) was re-run with the real busiest, `User_87` (7,331 events): mean cycle time 337.0 h to 336.4 h (-0.19 %), queue wait -0.9 %.
4. The README now leads with the findings, a results screenshot and the limitations.
5. Scenario presets (pooled allocation, halve delays, remove/copy the busiest person, demand +50 %) were added to the Scenario builder.
6. The **Data explorer** tab (work vs. waiting, idle time per task, common paths, hour-of-week heatmap, cases by month, handover matrix) and a **validation panel** on every result (fair reference, per-activity idle time, who holds the queues) were added; model version 3.
7. **CI** runs the tests and a JavaScript syntax check on every push (`.github/workflows/ci.yml`).
8. **LoanApp removed from the product.** It no longer appears in Process Lab's dataset list or the
   assistant's dataset list — a synthetic log doesn't belong in a real-data story. It stays
   registered internally (`user_facing=False`) because the test suite needs a fast, deterministic
   dataset for its end-to-end run; BPI 2017 is far too large for that. See `WORKBENCH.md`.
9. **The Simulation assistant is now one product with Process Lab**, not a separate page. It is a
   sidebar tab in the same single-page app, sharing the same design system; `/chat` redirects there.
   `webapp/static/index.html` (the old standalone dark-themed page) was removed.
10. **Two real bugs found by using the app, not just reading the code** (both documented with more
    detail in `WORKBENCH.md`): (a) "Learn resource profiles" always force-navigated to Resource
    profiles regardless of which tab you were on, which made the newly-added Data explorer look
    broken — it never showed anything because you were always bounced off it; fixed to return to
    the tab you launched it from, but only if you're still there (a background completion no longer
    yanks you off a tab you've since moved to, e.g. mid-conversation with the assistant). (b) The
    assistant never replied to any message ("Connection error" after a long hang) — not a code bug:
    the already-running server process had inherited a stale local proxy setting from an earlier
    VPN/corporate-tool session; restarting the server with a clean environment fixed it.
11. **Data Explorer now includes a Data Analyst**: nine (now ten) preset questions and free-text
    questions over the full BPI 2017 log (not just the `W_` work-item subset), covering outcomes
    (`A_Pending`/`A_Denied`/`A_Cancelled`), rework (`A_Incomplete`), offer negotiation, and observed
    customer-response time — the same offer/outcome/rework/customer-wait findings in §4 and §6 are
    reproducible live in the app, not just in this guide. Presets run with no LLM; free-text
    questions use DeepSeek only to produce a validated, typed query plan that deterministic code
    then executes — no generated code, SQL or numbers are trusted. A redundant, confusingly-labelled
    manual query-builder ("Build an analysis without AI") was removed: every preset already ran
    without AI, so the toggle implied the opposite of what was actually true and duplicated the
    presets with six dropdowns for no real benefit. A tenth preset, "Outcome mix by resource," was
    added in its place. See `WORKBENCH.md` ("Data Explorer as an analyst").
12. **Data Explorer's own overview charts (idle time, common paths, hour-of-week heatmap, case
    duration by month, handover matrix) were still `W_`-only** even after #11 gave the Data
    Analyst the full log — the two features loaded different data. Fixed: `discover()` now
    accepts an optional `full_events` frame (`webapp/workbench/full_log.py` streams the full XES
    the same way `outcomes.py` does, pairing `W_` start/complete lifecycles and treating `A_`/`O_`
    events as instantaneous) and uses it for these charts alone when available
    (`raw_data/BPIC_2017_W.full_log.csv`, `scripts/extract_full_log.py`) — model version 4.
    Resource profiles, calendars, templates and arrival days are computed from the `W_`-only frame
    exactly as before; only the descriptive charts changed. This is what surfaced the corrected
    19.1-day median cycle time in §3.2. Also fixed: `.actions` (the button row under the question
    box and under a result) had no `display:flex`/`gap` rule at all — buttons sat touching, with
    only a single collapsed inline-text space between them.

**Still open**
- **Keys:** `.env` holds ~13 credentials for several providers. It is git-ignored, but rotate any that were ever pasted into a chat, log or screenshot, and delete the ones this project does not use (it needs only DeepSeek and optionally LangSmith).
- **Calendars are shown in UTC.** Dutch local time is UTC+1/+2. Deliberately not changed: it would need a model version bump, a re-learn, and re-running every number here.
- **BPIC_2019** is listed but disabled; keep it as a documented data-quality lesson.
- **Research-engine cycle-time gap on BPI 2017** (section 6.1): try the paper's exact filtering, or trim the >7-day work items.
- **Warm-up:** pre-load the queue so short-horizon backlog is meaningful.
- **Auto-configuration instability** on LoanApp (report the spread or fix seeds).
- **Apply the duration fix to the model** (cap working time, treat the excess as delay) as a deliberate model-version change; it changes every headline number. Also unexplained: real cycle times fall for cases arriving in Nov-Dec 2016 beyond what cut-off can produce (seasonality vs drift cannot be separated with one year of data).

---

## 10. How to run everything

```powershell
cd AgentSimulator
./start-workbench.ps1                     # or: ./.venv/Scripts/python.exe -m webapp.main
# Process Lab  -> http://127.0.0.1:8000        (no API key)
# Chat agent   -> http://127.0.0.1:8000/chat   (needs deepseek_api_key in .env)
# API docs     -> http://127.0.0.1:8000/docs
```
1. Pick **BPIC_2017_W** → **Learn resource profiles** (~50 s first time, instant when cached).
2. Open a resource, click to add copies; or use **Activity changes** to scale a delay.
3. **Run baseline + scenario**, read **Results**, download the CSV of any repetition.

Research engine directly (LoanApp, ~2 min):
```powershell
./.venv/Scripts/python.exe simulate.py --log_path raw_data/LoanApp.csv.gz --case_id case_id --activity_name activity --resource_name resource --start_timestamp start_time --end_timestamp end_time --determine_automatically --num_simulations 10
./.venv/Scripts/python.exe evaluate_run.py LoanApp.csv 10
```
Regenerate the BPI 2017 CSV: `python raw_data/xes_to_csv.py raw_data/bpi2017/BPI_Challenge_2017.xes raw_data/BPIC_2017_W.csv --w-only`.
Tests: `./.venv/Scripts/python.exe -m pytest -q`.

---

## 11. Glossary

**Agent** — a software entity representing a resource. **Autonomous handover** — the current agent decides next task and next agent. **BPS** — business process simulation. **Calendar** — weekly working windows of a resource. **Case** — one process instance. **CTD/CTDD** — cycle-time distribution (distance). **Cycle time** — first start → last end of a case. **Empirical distribution** — sampling from observed values rather than a fitted formula. **Event log** — table of case/activity/resource/timestamps. **Extraneous / residual delay** — waiting not explained by resource contention or off-hours. **Orchestrated handover** — a central dispatcher picks the next activity. **Paired (common-random-number) replication** — baseline and scenario share random draws. **Process mining** — discovering, checking and improving processes from event logs. **Temporal split** — train on the past, test on the future. **Throughput** — cases completed per day. **Trace / variant** — the activity sequence of a case / a distinct such sequence. **Utilisation** — occupied working time ÷ available working time. **XES** — the XML standard format for event logs.

## 12. Reproducing the numbers in this guide

- Dataset statistics (§2–§4): read `raw_data/*.csv` with pandas; XES family/lifecycle counts by streaming `raw_data/bpi2017/BPI_Challenge_2017.xes`.
- Model statistics and experiments (§5.2, §6.2): `runs/workbench/models/95b66269….json`, `runs/workbench/experiments/*/result.json`.
- Research-engine scores (§6.1): `evaluate_run.py` on `simulated_data/LoanApp.csv/main_results/` and `simulated_data/BPIC_2017_W/main_results/`; auto-configuration values from `raw_data/bpi2017_run5.log`.
- Ad-hoc what-ifs (§6.3): `simulate()` and `compare()` from `webapp/workbench/simulation.py` on model `95b66269…`, 30 days, 2 repetitions, seed 42.
- Paper numbers: `agentsimulator.pdf` (arXiv 2408.08571), Tables I and II.
