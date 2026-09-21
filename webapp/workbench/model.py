"""Discover an inspectable empirical model from a temporally split event log.

This is the workbench's trace-resampling model, not the research simulator's
autonomous next-activity model. Frozen trace templates preserve observed paths
and preferred handoffs. Resource timings are sampled from historical work time.
"""
from collections import Counter, defaultdict
from bisect import bisect_right
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from .calendar import DAY, next_work, working_between

MODEL_VERSION = 2


def atomic_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, allow_nan=False, separators=(",", ":")), encoding="utf-8")
    temporary.replace(path)


def file_hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _samples(values):
    # Retain exact empirical frequency. A fixed quantile grid would substantially
    # overweight the single longest observation in a large resource history.
    return [round(float(x), 6) for x in sorted(values)]


def first_available(ready, observed_start, calendar, intervals):
    starts, ends = intervals
    current = next_work(ready, calendar)
    while current < observed_start:
        index = bisect_right(ends, current)
        if index == len(ends) or starts[index] - current >= 1:
            return current
        current = next_work(ends[index], calendar)
    return observed_start


def discover(frame: pd.DataFrame, dataset: str, model_id: str, source_hash: str) -> dict:
    required = ["case_id", "activity", "resource", "start", "end"]
    if any(c not in frame for c in required):
        raise ValueError("The log requires case ID, activity, resource, start and end columns.")
    df = frame[required].copy()
    if df[["case_id", "activity", "resource"]].isna().any().any():
        raise ValueError("The log contains missing case, activity or resource identifiers.")
    for col in ["case_id", "activity", "resource"]:
        df[col] = df[col].astype(str)
    for col in ["start", "end"]:
        df[col] = pd.to_datetime(df[col], utc=True, format="mixed", errors="coerce")
    if df[["start", "end"]].isna().any().any() or (df.end < df.start).any():
        raise ValueError("The log contains invalid timestamps or negative durations.")
    zero_fraction = float((df.end == df.start).mean())
    if zero_fraction == 1:
        raise ValueError("All events have zero duration. Supply measured start/end times before modeling resource capacity.")
    bounds = df.groupby("case_id").agg(start=("start", "min"), end=("end", "max")).sort_values("start")
    if len(bounds) < 10:
        raise ValueError("At least ten complete historical cases are required.")
    cutoff = bounds.iloc[min(len(bounds) - 1, int(len(bounds) * .8))].start
    train_ids = bounds.index[bounds.end < cutoff]
    test_ids = bounds.index[bounds.start >= cutoff]
    if len(train_ids) < 5 or not len(test_ids):
        raise ValueError("The temporal split has too few complete cases. Supply a longer history.")
    train = df[df.case_id.isin(train_ids)].sort_values(["case_id", "start", "end"]).copy()
    train["s"] = train.start.astype("int64") / 1e9
    train["e"] = train.end.astype("int64") / 1e9
    train["previous_case_end"] = train.groupby("case_id").e.cummax().groupby(train.case_id).shift()
    train_start, train_end = float(train.s.min()), float(train.e.max())
    profiles, by_id = [], {}
    durations, handoffs = defaultdict(list), defaultdict(Counter)
    elapsed_overlap = 0
    # Use both starts and completions to infer one observed working window per weekday.
    for index, (name, events) in enumerate(train.groupby("resource", sort=True)):
        points = pd.concat([events.start, events.end])
        calendar = []
        for weekday in range(7):
            hours = points[points.dt.weekday == weekday].dt.hour
            calendar.append([int(hours.min()) * 3600, min(24, int(hours.max()) + 1) * 3600] if len(hours) else None)
        rid = f"r{index}"
        ordered = events.sort_values("s")
        elapsed_overlap += int((ordered.s < ordered.e.cummax().shift()).sum())
        profile = dict(id=rid, name=name, event_count=len(events), case_count=int(events.case_id.nunique()),
                       calendar=calendar, activities={}, handoffs=[], template_id=rid)
        profiles.append(profile)
        by_id[name] = profile
    for row in train.itertuples():
        profile = by_id[row.resource]
        durations[(profile["id"], row.activity)].append(working_between(row.s, row.e, profile["calendar"]))
    for profile in profiles:
        for (rid, activity), values in durations.items():
            if rid == profile["id"]:
                profile["activities"][activity] = dict(count=len(values), mean_seconds=float(np.mean(values)),
                    median_seconds=float(np.median(values)), p90_seconds=float(np.quantile(values, .9)), samples=_samples(values))
        total = sum(sum(v) for (rid, _), v in durations.items() if rid == profile["id"])
        capacity = working_between(train_start, train_end, profile["calendar"])
        profile["observed_load_pct"] = 100 * total / capacity if capacity else 0
    # Find the first feasible window after a case was ready. The very last
    # resource completion before the observed start would erase earlier idle
    # windows (and wrongly explain customer delays as resource contention).
    occupied = {}
    for name, events in train.groupby("resource"):
        merged = []
        for start, end in events.sort_values("s")[["s", "e"]].itertuples(index=False, name=None):
            if end <= start:
                continue
            if merged and start <= merged[-1][1]:
                merged[-1][1] = max(end, merged[-1][1])
            else:
                merged.append([start, end])
        occupied[name] = ([i[0] for i in merged], [i[1] for i in merged])
    templates = []
    for _, events in train.groupby("case_id", sort=False):
        tasks, previous_resource = [], None
        for row in events.itertuples():
            profile = by_id[row.resource]
            delay = 0.0
            if tasks:
                ready = first_available(float(row.previous_case_end), row.s, profile["calendar"], occupied[row.resource])
                delay = max(0.0, row.s - ready)
                handoffs[previous_resource][profile["id"]] += 1
            tasks.append([row.activity, profile["id"], round(delay, 6)])
            previous_resource = profile["id"]
        templates.append(tasks)
    names = {p["id"]: p["name"] for p in profiles}
    for profile in profiles:
        counts = handoffs[profile["id"]]
        total = sum(counts.values())
        profile["handoffs"] = [dict(resource_id=rid, name=names[rid], probability=count / total)
                               for rid, count in counts.most_common(5)] if total else []
    arrivals = bounds.loc[train_ids].start.sort_values()
    daily = {d: g for d, g in arrivals.groupby(arrivals.dt.normalize())}
    arrival_days = [[] for _ in range(7)]
    for day in pd.date_range(arrivals.min().normalize(), arrivals.max().normalize(), freq="D"):
        observed = daily.get(day)
        offsets = sorted((observed.astype("int64") / 1e9 - day.timestamp()).tolist()) if observed is not None else []
        arrival_days[day.weekday()].append(offsets)
    test_cycle = (bounds.loc[test_ids].end - bounds.loc[test_ids].start).dt.total_seconds() / 3600
    activity_counts = train.activity.value_counts()
    model = dict(version=MODEL_VERSION, model_id=model_id, dataset=dataset, source_hash=source_hash,
        created_at=datetime.now(timezone.utc).isoformat(), engine="empirical-trace-v2",
        start_timestamp=float(bounds.loc[test_ids].start.min().normalize().timestamp()),
        source_events=len(df), source_cases=len(bounds), training_events=len(train), training_cases=len(train_ids),
        test_cases=len(test_ids), excluded_boundary_cases=len(bounds) - len(train_ids) - len(test_ids),
        training_start=pd.Timestamp(train_start, unit="s", tz="UTC").isoformat(),
        training_end=pd.Timestamp(train_end, unit="s", tz="UTC").isoformat(),
        overlap_event_pct=round(elapsed_overlap / len(train) * 100, 2), zero_duration_pct=round(zero_fraction * 100, 2),
        historical_cycle_hours=dict(mean=float(test_cycle.mean()), median=float(test_cycle.median()), p90=float(test_cycle.quantile(.9))),
        resources=profiles, activities=[dict(name=a, event_count=int(n)) for a, n in activity_counts.items()],
        templates=templates, arrival_days=arrival_days,
        assumptions=["Activity sequences and preferred resource handoffs are resampled from complete training cases.",
            "Resource durations are empirical working-time samples. Calendars are inferred hourly windows in UTC.",
            "Each resource handles one task at a time. Tasks pause outside working hours; parallel branches and multitasking are not modeled.",
            "Residual historical delays are estimated after resource contention and off-hours; they may include unobserved causes.",
            "New cases arrive during the selected horizon; execution continues until those cases finish. Every run starts with an empty queue.",
            "Historical validation statistics are descriptive. This model is not calibrated or causally validated for staffing decisions."])
    return model


def public_model(model):
    result = {k: v for k, v in model.items() if k not in {"templates", "arrival_days"}}
    result["resources"] = [{**p, "activities": {a: {k: v for k, v in d.items() if k != "samples"}
                         for a, d in p["activities"].items()}} for p in model["resources"]]
    return result
