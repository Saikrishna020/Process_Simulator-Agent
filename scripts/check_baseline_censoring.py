"""Is the Process Lab's ~20% baseline gap real, or an artefact of the log ending?

The held-out cases start after the temporal cutoff, but the log stops on a fixed date, so
late-arriving cases are cut short (right-censored) and look faster than they were. The
simulator runs every case to completion. This script compares both sides like for like.

    python scripts/check_baseline_censoring.py [model_id]

Needs raw_data/BPIC_2017_W.csv and a learned model in runs/workbench/models/.
"""
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from webapp.workbench.schemas import ExperimentRequest  # noqa: E402
from webapp.workbench.simulation import simulate  # noqa: E402

MODEL_ID = sys.argv[1] if len(sys.argv) > 1 else "95b66269d49a5e77a4db934b"
FULL_FOLLOW_UP_DAYS = 75  # nearly every case (>99%) finishes well within this
SEEDS = range(42, 47)


def summary(hours):
    return f"{hours.mean():6.1f} {hours.median():6.1f} {hours.quantile(.9):6.1f}"


def real_cases():
    log = pd.read_csv(ROOT / "raw_data" / "BPIC_2017_W.csv")
    for column in ["start_timestamp", "end_timestamp"]:
        log[column] = pd.to_datetime(log[column], utc=True, format="mixed")
    cases = log.groupby("case_id").agg(s=("start_timestamp", "min"), e=("end_timestamp", "max")).sort_values("s")
    cases["h"] = (cases.e - cases.s).dt.total_seconds() / 3600
    cutoff = cases.iloc[int(len(cases) * .8)].s  # same rule as webapp/workbench/model.py
    return cases, cutoff, log.end_timestamp.max()


def main():
    cases, cutoff, log_end = real_cases()
    train, test = cases[cases.e < cutoff], cases[cases.s >= cutoff].copy()
    test["follow"] = (log_end - test.s).dt.total_seconds() / 86400
    test["week"] = ((test.s - cutoff).dt.days // 7)
    fair = test[test.follow >= FULL_FOLLOW_UP_DAYS]
    early = cases[(cases.s < pd.Timestamp("2016-08-15", tz="UTC")) & (cases.e < cutoff)]
    print(f"log ends {log_end:%Y-%m-%d}; test cases arrive {cutoff:%Y-%m-%d} to {test.s.max():%Y-%m-%d}\n")

    print("REAL cycle time (hours)        mean median    p90      cases")
    for label, frame in [("as evaluated (all test)", test), (f">= {FULL_FOLLOW_UP_DAYS} d follow-up (fair)", fair),
                         ("early training, long follow-up", early)]:
        print(f"  {label:32s}{summary(frame.h)}  {len(frame):6d}")
    print("\nREAL test cases by arrival week (mean h, follow-up left):")
    weekly = test.groupby("week").agg(n=("h", "size"), mean=("h", "mean"), follow=("follow", "min")).round(1)
    print(weekly.to_string())

    model = json.load(open(ROOT / "runs" / "workbench" / "models" / f"{MODEL_ID}.json"))
    anchor = model["start_timestamp"]
    obs_end_day = (log_end.timestamp() - anchor) / 86400
    horizon = int(np.ceil((test.s.max().timestamp() - anchor) / 86400)) + 1
    frames, waits = [], []
    for seed in SEEDS:
        request = ExperimentRequest(model_id=MODEL_ID, name="censoring check", horizon_days=horizon, repetitions=1, seed=seed)
        _, events = simulate(model, request, seed, baseline=True)
        table = pd.DataFrame(events)
        per_case = table.groupby("case_id").agg(a=("arrival_timestamp", "first"), e=("end_timestamp", "max"))
        per_case["full"] = (per_case.e - per_case.a) / 3600
        per_case["seen"] = (np.minimum(per_case.e, log_end.timestamp()) - per_case.a) / 3600  # cut at the real log end
        per_case["follow"] = obs_end_day - (per_case.a - anchor) / 86400
        frames.append(per_case)
        waits.append(table.groupby("resource").queue_seconds.sum() / 3600)
    sim = pd.concat(frames)
    print("\nSIMULATED baseline, same arrival window (mean, median, p90):")
    print(f"  run to completion (what the Lab reports)  {summary(sim.full)}")
    print(f"  cut at the real log end (like the test)   {summary(sim.seen)}")
    print(f"  effect of the cut-off on the mean: {sim.seen.mean() - sim.full.mean():+.1f} h "
          f"({100 * (sim.seen.mean() / sim.full.mean() - 1):+.1f}%); real data: "
          f"{test.h.mean() - fair.h.mean():+.1f} h ({100 * (test.h.mean() / fair.h.mean() - 1):+.1f}%)")

    print(f"\nGAP, simulated (to completion) vs real:      mean   median    p90")
    for label, real in [("as the Lab shows it (all test)", test.h), ("fair reference", fair.h)]:
        gaps = [100 * (getattr(sim.full, f)() / getattr(real, f)() - 1) if f != "p90" else
                100 * (sim.full.quantile(.9) / real.quantile(.9) - 1) for f in ("mean", "median", "p90")]
        print(f"  vs {label:34s}{gaps[0]:+6.1f}% {gaps[1]:+7.1f}% {gaps[2]:+6.1f}%")

    total = pd.concat(waits, axis=1).mean(axis=1).sort_values(ascending=False)
    print(f"\nQueue hours: top 5 resources hold {100 * total.head(5).sum() / total.sum():.0f}% of all queueing "
          f"({', '.join(total.head(5).index)})")


if __name__ == "__main__":
    main()
