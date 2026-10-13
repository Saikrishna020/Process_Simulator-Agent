# Process Lab — the background guide

*For someone who has never seen this project and may know nothing about process mining.*

This document holds the **domain background** — what process mining is, what the datasets
contain, and what the BPI 2017 data actually says. It is deliberately engine-agnostic.

**The [README](../README.md) is the single source of truth for architecture, method, results,
scoring, limitations and how to run everything.** It is not repeated here: two overlapping
descriptions of the same system drift apart, which is the exact class of bug that consolidating
to one engine (and one shared temporal split) was meant to remove.

One note on history, since interviews ask about it: earlier versions of this repository ran the
*AgentSimulator* paper's own supplementary code (`source/`, Mesa-based) alongside this engine.
That engine could not change staffing, schedules, demand or activity speed, so it could not
answer what-if questions; it was removed and its five published distance measures were ported
onto this engine instead. The README's "One engine, deliberately" section covers the trade-off,
including what was given up.

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
  *extraneous / residual delay* = the task simply wasn't started yet for reasons the log can't see, e.g. waiting for a customer to send a document (**not fixable with capacity**). Distinguishing these is the crux of §4 below.
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
2. **Simulate** the test period, generate a synthetic log, and **score** it against the held-out real log with distance metrics (see the README, "Scoring"). Lower = more realistic.
3. Only then run counterfactuals.

### 1.4 Concepts worth being able to explain in an interview

- **Why temporal, not random, split?** Processes drift; random splits let the model "see" the future.
- **Why paired random numbers (common random numbers)?** Baseline and scenario reuse the same arrivals and the same random draws per case, so their *difference* has far lower variance than two independent runs. A scenario with no changes reproduces the baseline **exactly** (there is a test for this).
- **Why "empirical" durations?** The workbench samples from the real observed working-time list instead of fitting a Gamma/Log-Normal — no distributional assumption, and rare long tasks stay represented.
- **Why subtract off-hours from durations?** A task that started Friday 16:00 and ended Monday 09:00 did not take 65 hours of work. Only time inside the person's calendar counts.
- **What is a "cool-down"/"warm-up" problem?** Starting the simulation with an empty system understates queues at the beginning; ending it at a fixed horizon leaves unfinished cases. Both matter for the *backlog* metric here (the README lists it under Limitations).

---

---

## 2. The datasets

Three logs are registered (`processlab/core/datasets.py`). One file each in `raw_data/`.
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

Only **W_** items describe *human work with durations*, which is what resource simulation needs, so `processlab/extract/full_log.py` keeps them. Crucially, XES stores each work item as **several lifecycle events** — `schedule` (149k), `start` (128k), `suspend` (215k), `resume` (127k), `complete` (475k), `ate_abort` (85k), `withdraw` (22k). The converter **pairs `start/resume → complete/suspend` into intervals**, ignoring `schedule/withdraw/ate_abort`, producing **254,382 intervals** with a real start and end. That is the flat 5-column CSV both engines consume.

The eight activities (interval counts): `W_Validate application` 72,937 · `W_Call incomplete files` 62,260 · `W_Call after offers` 60,560 · `W_Complete application` 52,321 · `W_Handle leads` 4,891 · `W_Assess potential fraud` 1,323 · `W_Shortened completion` 81 · `W_Personal Loan collection` 9.

> **Note:** the paper reports 30,276 traces / 240,854 events for "BPI17W". Our extraction has 31,500 / 254,382, i.e. a slightly different pairing/filtering. Results are therefore *close to* but not *identical to* the paper's benchmark (see the README).

### 2.3 BPI Challenge 2019 (real, large, unusable for capacity — a lesson)
Purchase-order handling: `Create Purchase Order Item`, `Record Goods Receipt`, `Record Invoice Receipt`, `Clear Invoice`, `SRM: …` and 35 others. Problems, all verified:
- **Every event is zero-duration** (`start == end`) — the source XES has no start/complete lifecycle, so the converter treats each as instantaneous. Without durations, "how long does this person take" and "how busy are they" cannot be learned. The Process Lab refuses it with an explanation.
- **25 % of events (399,090) have resource `NONE`**; `user_002` alone did 10 %; many `batch_xx` are automated.
- Timestamps go back to **1948** — obvious data-entry anomalies.
- The research-engine runs on it failed (see `raw_data/bpi2019_run*.log`, incl. the Windows `mkdir` bug fixed later) and **no `simulated_log_*.csv` exists for it** — only the pre-processed train/test split.
- **Lesson worth stating in a portfolio:** *fit-for-purpose data check comes before modelling.* A simulator that can't tell a log has no durations would silently produce nonsense.

---

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

**This table understates the true cycle time.** It's built from `W_` work items only — the last recorded event of a case is often the last thing a *human* did, not when the case actually closed. Data Explorer now also loads the full log (`A_`/`O_`/`W_` events, via `scripts/extract_full_log.py`) and the true median jumps to **19.1 days** (mean 21.9, p90 35.1) — nearly double. Touch-time share drops from 0.9% to 0.5%: waiting is even more dominant than this table shows. The gap is time between the last work item and the case's formal close (an automated step, or the customer simply not responding) — itself a finding, not an artefact. The README has the detail; the simulator's own model (see the README) is unaffected, since it correctly uses only `W_` events for resource capacity regardless.

### 3.3 People: many generalists, moderate concentration, calendar of a real office
- **149 resources.** Top 10 do 24 % of events, top 20 do 39 %, the bottom half of the workforce does only 13 %.
- A typical resource does **~5 of the 8 activity types**. Only 19 % are specialists (≥80 % of their work in one activity). Yet there *are* stars: `User_87` does 7,331 items (mostly `Validate application`), `User_5` 5,980 (mostly `Call after offers`).
- Handoffs are sticky: 26.8 % of the time the next item goes to the **same person**; `User_87` hands to themself 42 % of the time.
- Rhythm: 93 % of work is Mon–Fri, Saturday 6.6 %, Sunday 0.2 %. 89 % of items start between 07:00 and 17:59 **UTC** (Dutch local time is +1/+2 h — the README notes this under Limitations).
- 15.4 % of work items overlap with another item of the same person (multitasking) — a direct violation of the "one task at a time" assumption of most simulators.

### 3.4 Data quality notes
- 0.16 % of work items last more than a day (max 136 days — pairing artefacts); `W_Personal Loan collection` (9 items) has a median of ~20 days.
- `W_Shortened completion ` has a **trailing space** in its name in the data.
- Resource names are anonymised (`User_n`).

---

---

## 4. The insight that shapes everything: capacity is not the bottleneck

Combine §3.2 and §3.3: hands-on time is 0.9 % of elapsed time and the average simulated utilisation is ~6 %. The Process Lab's discovery step classifies each historical wait as either *queue wait* (the resource was busy / off-shift) or *residual delay* (the resource was free but nothing happened — the customer, an approval, a system…). On BPI 2017 v2 of the model:

- **Mean residual delay per case: 263 h**, **mean queue+calendar wait: 71 h**, in a **337 h** simulated mean cycle.
- i.e. **~78 % of cycle time is external delay; ~21 % is queueing; processing is ~1 %.**

That is why the what-if results read the way they do (see the README, "What the data actually says"). The now-removed research engine independently agreed: with extraneous delays switched *on* its cycle-time distance on the validation window was **~90 vs. ~298** with them *off* — a 3x improvement — and its automatic configuration chose "delays on". Two different engines reaching the same conclusion about where the time goes is the strongest evidence in this project.

---

---

## 5. Glossary

**Agent** — a software entity representing a resource. **Autonomous handover** — the current agent decides next task and next agent. **BPS** — business process simulation. **Calendar** — weekly working windows of a resource. **Case** — one process instance. **CTD/CTDD** — cycle-time distribution (distance). **Cycle time** — first start → last end of a case. **Empirical distribution** — sampling from observed values rather than a fitted formula. **Event log** — table of case/activity/resource/timestamps. **Extraneous / residual delay** — waiting not explained by resource contention or off-hours. **Orchestrated handover** — a central dispatcher picks the next activity. **Paired (common-random-number) replication** — baseline and scenario share random draws. **Process mining** — discovering, checking and improving processes from event logs. **Temporal split** — train on the past, test on the future. **Throughput** — cases completed per day. **Trace / variant** — the activity sequence of a case / a distinct such sequence. **Utilisation** — occupied working time ÷ available working time. **XES** — the XML standard format for event logs.
