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

MODEL_VERSION = 3


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


def _quantiles(values):
    values = pd.Series(values)
    return dict(mean=float(values.mean()), median=float(values.median()), p90=float(values.quantile(.9)))


def _gaps(events):
    """Idle time before each task: start minus the latest earlier completion in the same case."""
    ordered = events.sort_values(["case_id", "start", "end"])
    start, end = ordered.start.astype("int64") / 1e9, ordered.end.astype("int64") / 1e9
    previous_end = end.groupby(ordered.case_id).cummax().groupby(ordered.case_id).shift()
    return ordered.assign(gap=(start - previous_end).clip(lower=0) / 3600, previous_end=previous_end)


def reference(df, bounds, train_ids, test_ids):
    """Held-out history that a simulation can fairly be compared with.

    The log stops on a fixed date, so held-out cases that arrive shortly before it are cut short
    and look faster than they were. Only cases with at least as much follow-up time as 99.5% of
    the training cases needed are used. Falls back to all held-out cases (flagged) if too few remain.
    """
    train = bounds.loc[train_ids]
    test = bounds.loc[test_ids]
    follow_up = float((train.end - train.start).dt.total_seconds().quantile(.995))
    seen = test[(df.end.max() - test.start).dt.total_seconds() >= follow_up]
    complete = len(seen) >= max(30, .1 * len(test))
    chosen = seen if complete else test
    events = _gaps(df[df.case_id.isin(chosen.index)])
    later = events[events.previous_end.notna()]
    span_days = max(1, (test.start.max() - test.start.min()).days + 1)
    return dict(cases=len(chosen), all_test_cases=len(test), follow_up_days=round(follow_up / 86400, 1), censoring_controlled=bool(complete),
        cycle_hours=_quantiles((chosen.end - chosen.start).dt.total_seconds() / 3600),
        all_test_cycle_hours=_quantiles((test.end - test.start).dt.total_seconds() / 3600),
        events_per_case=float(len(events) / len(chosen)), arrivals_per_day=float(len(test) / span_days),
        activity_gaps={a: dict(events=int(len(g)), mean_hours=float(g.gap.mean()), median_hours=float(g.gap.median()),
                               p90_hours=float(g.gap.quantile(.9))) for a, g in later.groupby("activity")})


def explore(df, bounds):
    """Descriptive statistics of the whole log for the data explorer (not used by the simulation)."""
    events = _gaps(df)
    events["seconds"] = (events.end - events.start).dt.total_seconds()
    cycle = (bounds.end - bounds.start).dt.total_seconds()
    by_case = events.groupby("case_id", sort=False)
    variants = by_case.activity.agg(tuple).value_counts()
    top = variants.head(8)
    hours = events.groupby([events.start.dt.weekday, events.start.dt.hour]).size()
    week = [[int(hours.get((d, h), 0)) for h in range(24)] for d in range(7)]
    months = bounds.assign(cycle_days=cycle / 86400).groupby(bounds.start.dt.strftime("%Y-%m")).cycle_days.agg(["size", "median"])
    busiest = events.resource.value_counts().head(12).index.tolist()
    events["previous_resource"] = by_case.resource.shift()
    moves = events[events.previous_resource.notna()]
    totals = moves.groupby("previous_resource").size()
    counts = moves[moves.previous_resource.isin(busiest)].groupby(["previous_resource", "resource"]).size()
    matrix = [[float(counts.get((a, b), 0) / totals[a]) if a in totals else 0.0 for b in busiest] for a in busiest]
    activities = [dict(name=a, events=int(len(g)), share=float(len(g) / len(events)), median_minutes=float(g.seconds.median() / 60),
                       median_gap_hours=float(g.gap.median()) if g.gap.notna().any() else 0.0,
                       mean_gap_hours=float(g.gap.mean()) if g.gap.notna().any() else 0.0)
                  for a, g in events.groupby("activity") if len(g)]
    return dict(cases=len(bounds), events=len(events), activity_count=int(events.activity.nunique()), resource_count=int(events.resource.nunique()),
        first_start=df.start.min().isoformat(), last_end=df.end.max().isoformat(),
        cycle_days=dict(mean=float(cycle.mean() / 86400), median=float(cycle.median() / 86400), p90=float(cycle.quantile(.9) / 86400)),
        median_touch_minutes=float(by_case.seconds.sum().median() / 60),
        touch_share_pct=float(100 * events.seconds.sum() / cycle.sum()) if cycle.sum() else 0.0,
        repeat_case_pct=float(100 * events.duplicated(["case_id", "activity"]).groupby(events.case_id).any().mean()),
        variant_count=int(len(variants)), top_variant_coverage_pct=float(100 * top.sum() / len(bounds)),
        variants=[dict(steps=list(v), cases=int(n), share=float(n / len(bounds))) for v, n in top.items()],
        activities=sorted(activities, key=lambda a: -a["events"]), hour_of_week=week,
        months=[dict(month=m, cases=int(r["size"]), median_cycle_days=float(r["median"])) for m, r in months.iterrows()],
        handover=dict(names=busiest, matrix=matrix, same_resource_pct=float(100 * (moves.resource == moves.previous_resource).mean())))


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
    history = reference(df, bounds, train_ids, test_ids)
    activity_counts = train.activity.value_counts()
    model = dict(version=MODEL_VERSION, model_id=model_id, dataset=dataset, source_hash=source_hash,
        created_at=datetime.now(timezone.utc).isoformat(), engine="empirical-trace-v3",
        start_timestamp=float(bounds.loc[test_ids].start.min().normalize().timestamp()),
        source_events=len(df), source_cases=len(bounds), training_events=len(train), training_cases=len(train_ids),
        test_cases=len(test_ids), excluded_boundary_cases=len(bounds) - len(train_ids) - len(test_ids),
        training_start=pd.Timestamp(train_start, unit="s", tz="UTC").isoformat(),
        training_end=pd.Timestamp(train_end, unit="s", tz="UTC").isoformat(),
        overlap_event_pct=round(elapsed_overlap / len(train) * 100, 2), zero_duration_pct=round(zero_fraction * 100, 2),
        historical_cycle_hours=history["cycle_hours"], reference=history, explore=explore(df, bounds),
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
