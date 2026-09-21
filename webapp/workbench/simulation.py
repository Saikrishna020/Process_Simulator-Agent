"""Seeded, capacity-constrained scenario simulation with paired replications."""
from collections import defaultdict
from copy import deepcopy
import heapq
import math
import random
import time

import numpy as np

from .calendar import DAY, finish_work, next_work, working_between
from .schemas import ExperimentRequest


def scenario_resources(model: dict, request: ExperimentRequest | None):
    resources = {p["id"]: {**deepcopy(p), "duration_multiplier": 1.0} for p in model["resources"]}
    if request is None:
        return resources
    known = set(resources)
    for change in request.resource_changes:
        if change.resource_id not in known:
            raise ValueError(f"Unknown resource: {change.resource_id}")
        if not change.enabled:
            resources.pop(change.resource_id)
        else:
            resource = resources[change.resource_id]
            resource["duration_multiplier"] = change.duration_multiplier
            if change.schedule:
                resource["calendar"] = change.schedule.calendar()
    original = {p["id"]: p for p in model["resources"]}
    names = {p["name"] for p in resources.values()}
    for index, clone in enumerate(request.clones):
        if clone.source_id not in known:
            raise ValueError(f"Unknown resource template: {clone.source_id}")
        for number in range(clone.count):
            name = clone.name if clone.count == 1 else f"{clone.name} {number + 1}"
            if name in names:
                raise ValueError(f"Resource name already exists: {name}")
            names.add(name)
            rid = f"clone_{index}_{number}"
            resources[rid] = {**deepcopy(original[clone.source_id]), "id": rid, "name": name,
                "template_id": clone.source_id, "duration_multiplier": clone.duration_multiplier}
            if clone.schedule:
                resources[rid]["calendar"] = clone.schedule.calendar()
    activities = {a["name"] for a in model["activities"]}
    for change in request.activity_changes:
        if change.activity not in activities:
            raise ValueError(f"Unknown activity: {change.activity}")
    covered = {a for r in resources.values() for a in r["activities"]}
    missing = activities - covered
    if missing:
        raise ValueError("No active resource can perform: " + ", ".join(sorted(missing)))
    return resources


def arrival_plan(model: dict, days: int, demand: float, seed: int):
    """Pair arrivals across scenarios: the first N cases each day share random draws."""
    plans = []
    anchor = model["start_timestamp"]
    for day in range(days):
        start = anchor + day * DAY
        weekday = (math.floor(start / DAY) + 3) % 7
        historical_days = model["arrival_days"][weekday]
        if not historical_days:
            continue
        daily_rng = random.Random(seed * 1000003 + day * 9176)
        offsets = daily_rng.choice(historical_days)
        count = round(len(offsets) * demand)
        for index in range(count):
            case_seed = seed * 1000000007 + day * 100003 + index
            rng = random.Random(case_seed)
            plans.append(dict(case_id=f"d{day + 1}_c{index + 1}", arrival=start + rng.choice(offsets),
                              template=rng.randrange(len(model["templates"])), seed=case_seed))
    if not plans:
        raise ValueError("No cases arrived in this sampled horizon. Increase the horizon or choose another seed.")
    events = sum(len(model["templates"][p["template"]]) for p in plans)
    if len(plans) > 30000 or events > 300000:
        raise ValueError("This scenario exceeds 30,000 cases or 300,000 events per repetition. Reduce days or demand.")
    return plans


def simulate(model: dict, request: ExperimentRequest, seed: int, *, baseline=False, deadline=None):
    resources = scenario_resources(model, None if baseline else request)
    demand = 1 if baseline else request.demand_multiplier
    routing = "historical" if baseline else request.routing
    activity_changes = {} if baseline else {c.activity: c for c in request.activity_changes}
    plan = arrival_plan(model, request.horizon_days, demand, seed)
    begin = model["start_timestamp"]
    horizon = begin + request.horizon_days * DAY
    available = {rid: begin for rid in resources}
    by_activity = defaultdict(list)
    by_template = defaultdict(list)
    for rid, resource in resources.items():
        by_template[resource["template_id"]].append(rid)
        for activity in resource["activities"]:
            by_activity[activity].append(rid)
    queue = [(p["arrival"], index, 0) for index, p in enumerate(plan)]
    heapq.heapify(queue)
    events, cycles = [], []
    case_wait = [0.0] * len(plan)
    case_delay = [0.0] * len(plan)
    resource_work = defaultdict(float)
    resource_events = defaultdict(int)
    activity_wait = defaultdict(list)
    completed = 0
    while queue:
        if len(events) % 1000 == 0 and deadline and time.monotonic() > deadline:
            raise ValueError("Experiment exceeded the 15-minute limit. Reduce the horizon or repetition count.")
        ready, case_index, task_index = heapq.heappop(queue)
        case = plan[case_index]
        tasks = model["templates"][case["template"]]
        activity, preferred, delay = tasks[task_index]
        change = activity_changes.get(activity)
        # 'ready' already includes this activity's residual delay.
        candidates = by_template.get(preferred, []) if routing == "historical" else []
        if not candidates:
            candidates = by_activity[activity]
        rid = min(candidates, key=lambda key: (next_work(max(ready, available[key]), resources[key]["calendar"]), available[key], key))
        resource = resources[rid]
        values = resource["activities"][activity]["samples"]
        quantile = random.Random(case["seed"] + (task_index + 1) * 7919).random()
        work = values[min(len(values) - 1, int(quantile * len(values)))] * resource["duration_multiplier"]
        if change:
            work *= change.duration_multiplier
        start, end = finish_work(max(ready, available[rid]), work, resource["calendar"])
        if end > horizon + 5 * 366 * DAY:
            raise ValueError("Backlog extends more than five years. Add capacity or reduce demand.")
        available[rid] = end
        wait = start - ready
        case_wait[case_index] += wait
        resource_work[rid] += working_between(start, min(end, horizon), resource["calendar"])
        if start < horizon:
            resource_events[rid] += 1
        activity_wait[activity].append(wait / 3600)
        events.append(dict(case_id=case["case_id"], activity=activity, resource_id=rid, resource=resource["name"],
                           arrival_timestamp=case["arrival"], ready_timestamp=ready, start_timestamp=start,
                           end_timestamp=end, processing_seconds=work, queue_seconds=wait,
                           residual_delay_seconds=delay * (change.delay_multiplier if change else 1)))
        if task_index + 1 == len(tasks):
            cycles.append((end - case["arrival"]) / 3600)
            completed += int(end <= horizon)
        else:
            next_activity, _, next_delay = tasks[task_index + 1]
            next_change = activity_changes.get(next_activity)
            next_delay *= next_change.delay_multiplier if next_change else 1
            case_delay[case_index] += next_delay
            heapq.heappush(queue, (end + next_delay, case_index, task_index + 1))
    total_capacity = sum(working_between(begin, horizon, r["calendar"]) for r in resources.values())
    metrics = dict(arrived_cases=len(plan), completed_in_horizon=completed, backlog_at_horizon=len(plan) - completed,
        throughput_per_day=completed / request.horizon_days, mean_cycle_hours=float(np.mean(cycles)),
        median_cycle_hours=float(np.median(cycles)), p90_cycle_hours=float(np.quantile(cycles, .9)),
        mean_wait_hours=float(np.mean(case_wait) / 3600), mean_residual_delay_hours=float(np.mean(case_delay) / 3600),
        sla_met_pct=100 * sum(c <= request.sla_hours for c in cycles) / len(cycles),
        utilization_pct=100 * sum(resource_work.values()) / total_capacity if total_capacity else 0,
        active_resources=len(resources))
    resource_metrics = [dict(id=rid, name=r["name"], template_id=r["template_id"],
        utilization_pct=100 * resource_work[rid] / max(1, working_between(begin, horizon, r["calendar"])),
        started_tasks=resource_events[rid]) for rid, r in resources.items()]
    return dict(seed=seed, metrics=metrics, resources=resource_metrics,
                activities=[dict(name=a, mean_wait_hours=float(np.mean(v))) for a, v in activity_wait.items()]), events


def compare(baselines, scenarios):
    comparison = {}
    for key in baselines[0]["metrics"]:
        before = np.array([r["metrics"][key] for r in baselines])
        after = np.array([r["metrics"][key] for r in scenarios])
        def stats(values):
            return dict(mean=float(values.mean()), min=float(values.min()), max=float(values.max()), std=float(values.std()))
        difference = after - before
        comparison[key] = dict(baseline=stats(before), scenario=stats(after), delta=stats(difference),
                               change_pct=float(100 * difference.mean() / before.mean()) if before.mean() else None)
    resource_ids = sorted({r["id"] for run in baselines + scenarios for r in run["resources"]})
    resources = []
    for rid in resource_ids:
        sides = [[r for run in runs for r in run["resources"] if r["id"] == rid] for runs in [baselines, scenarios]]
        present = next(rows[0] for rows in sides if rows)
        resources.append(dict(id=rid, name=present["name"], baseline_utilization=float(np.mean([r["utilization_pct"] for r in sides[0]])) if sides[0] else None,
                              scenario_utilization=float(np.mean([r["utilization_pct"] for r in sides[1]])) if sides[1] else None))
    activity_names = sorted({a["name"] for run in baselines + scenarios for a in run["activities"]})
    activities = []
    for name in activity_names:
        item = {"name": name}
        for label, runs in [("baseline", baselines), ("scenario", scenarios)]:
            values = [a["mean_wait_hours"] for r in runs for a in r["activities"] if a["name"] == name]
            item[label] = float(np.mean(values)) if values else 0
        activities.append(item)
    return dict(metrics=comparison, resources=resources, activities=activities)
