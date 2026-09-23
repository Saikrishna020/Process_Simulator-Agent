"""Typed local analytics. The language model plans; deterministic functions calculate.

No model-generated code, SQL, paths or numbers are executed or trusted.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import threading
from typing import Literal
import uuid

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, model_validator

from webapp.orchestrator.dataset_registry import get_dataset
from .model import atomic_json, file_hash, _gaps
from .outcomes import DEFINITIONS
from .service import BusyError, read_json

FieldName = Literal["outcome", "arrival_month", "rework_band", "offer_band", "activity", "resource", "weekday",
                    "cycle_days", "rework_count", "offer_count", "customer_response_hours", "task_minutes", "gap_hours", "event_count"]
Group = Literal["outcome", "arrival_month", "rework_band", "offer_band", "activity", "resource", "weekday"]
Metric = Literal["count", "cycle_days", "rework_count", "offer_count", "customer_response_hours", "task_minutes", "gap_hours", "event_count"]


class Filter(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    field: FieldName
    operator: Literal["eq", "gte", "lte", "in"] = "eq"
    value: str | float | list[str | float]

    @model_validator(mode="after")
    def valid_membership(self):
        if self.operator == "in":
            if not isinstance(self.value, list) or not 1 <= len(self.value) <= 20:
                raise ValueError("An 'in' filter requires between 1 and 20 values.")
        elif isinstance(self.value, list):
            raise ValueError("Use 'in' to filter by multiple values.")
        return self


class AnalysisPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    table: Literal["cases", "events"] = "cases"
    metric: Metric = "count"
    aggregation: Literal["count", "mean", "median", "p90"] = "count"
    group_by: Group | None = "outcome"
    split_by: Group | None = None
    filters: list[Filter] = Field(default_factory=list, max_length=6)
    chart: Literal["bar", "line", "table"] = "bar"
    order: Literal["value_desc", "value_asc", "group"] = "value_desc"
    limit: int = Field(default=20, ge=1, le=40)

    @model_validator(mode="after")
    def coherent(self):
        if (self.metric == "count") != (self.aggregation == "count"):
            raise ValueError("Use count/count for counts; mean, median or p90 for numeric metrics.")
        if self.split_by and (not self.group_by or self.split_by == self.group_by):
            raise ValueError("A second grouping must differ from the first.")
        if self.chart == "line" and self.group_by != "arrival_month":
            raise ValueError("Line charts require arrival_month on the horizontal axis.")
        if self.chart == "line" and self.split_by:
            raise ValueError("Use a bar chart or table for a second grouping.")
        return self


class Intent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan: AnalysisPlan | None = None
    clarification: str = ""


class AnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_id: str = Field(pattern=r"^[a-f0-9]{24}$")
    question: str = Field(default="", max_length=2000)
    plan: AnalysisPlan | None = None
    previous_plan: AnalysisPlan | None = None

    @model_validator(mode="after")
    def has_question(self):
        if not self.plan and not self.question.strip():
            raise ValueError("Enter a question or select an analysis.")
        return self


PRESETS = [
    ("Outcome distribution", dict(group_by="outcome")),
    ("Cycle time by outcome", dict(metric="cycle_days", aggregation="median", group_by="outcome")),
    ("Customer response time by outcome", dict(metric="customer_response_hours", aggregation="median", group_by="outcome")),
    ("Does rework accompany longer cases?", dict(metric="cycle_days", aggregation="median", group_by="rework_band", order="group")),
    ("Compare rework within each outcome", dict(metric="cycle_days", aggregation="median", group_by="rework_band", split_by="outcome", order="group")),
    ("Offers versus outcome", dict(group_by="offer_band", split_by="outcome", order="group")),
    ("Which tasks have the longest gaps?", dict(table="events", metric="gap_hours", aggregation="mean", group_by="activity")),
    ("Who handles the most recorded tasks?", dict(table="events", group_by="resource")),
    ("Outcome mix by resource", dict(table="events", group_by="resource", split_by="outcome")),
    ("How does case duration change by month?", dict(metric="cycle_days", aggregation="median", group_by="arrival_month", chart="line", order="group")),
]


class DataCatalog:
    def __init__(self):
        self.lock = threading.Lock()
        self.cache = {}

    def load(self, store, model_id):
        model = store.get_model(model_id)
        dataset = get_dataset(model["dataset"])
        if not dataset:
            raise ValueError("This dataset is not registered.")
        source = store.raw_data / dataset.relative_log_path
        companion = store.raw_data / f"{dataset.name}.outcomes.csv"
        metadata_path = companion.with_suffix(".json")
        paths = [source, companion, metadata_path]
        signature = tuple((str(p.resolve()), p.stat().st_mtime_ns, p.stat().st_size) if p.exists() else (str(p), None, None) for p in paths)
        key = (model_id, signature)
        with self.lock:
            if key in self.cache:
                return self.cache[key]
            if file_hash(source) != model["source_hash"]:
                raise ValueError("The source log changed. Learn resource profiles again before analysing it.")
            mapping = {dataset.case_id: "case_id", dataset.activity_name: "activity", dataset.resource: "resource",
                       dataset.start_timestamp: "start", dataset.end_timestamp: "end"}
            events = pd.read_csv(source, usecols=list(mapping), dtype=str).rename(columns=mapping)
            for col in ("start", "end"):
                events[col] = pd.to_datetime(events[col], utc=True, format="mixed", errors="raise")
            events = _gaps(events)
            events["task_minutes"] = (events.end - events.start).dt.total_seconds() / 60
            events["gap_hours"] = events.gap
            events["weekday"] = events.start.dt.day_name()
            cases = events.groupby("case_id").agg(first_start=("start", "min"), last_end=("end", "max"), event_count=("activity", "size"))
            cases["cycle_days"] = (cases.last_end - cases.first_start).dt.total_seconds() / 86400
            coverage, metadata = 0, None
            if companion.exists():
                if not metadata_path.exists():
                    raise ValueError("Outcomes provenance is missing. Run scripts/extract_outcomes.py again.")
                metadata = read_json(metadata_path)
                if metadata.get("csv_sha256") != file_hash(companion):
                    raise ValueError("The outcomes CSV changed without matching provenance. Extract outcomes again.")
                outcomes = pd.read_csv(companion, dtype={"case_id": str})
                if outcomes.case_id.duplicated().any():
                    raise ValueError("Outcomes must contain exactly one row per case ID.")
                outcomes = outcomes.set_index("case_id")
                coverage = int(cases.index.isin(outcomes.index).sum())
                # Full-log cycle dates replace work-item dates only for matched cases.
                cases = cases[["event_count"]].join(outcomes, how="left", validate="one_to_one")
                for col in ("first_start", "last_end"):
                    cases[col] = pd.to_datetime(cases[col], utc=True, format="mixed")
                cases["outcome"] = cases.outcome.fillna("Unavailable")
                for col in ("rework_count", "offer_count"):
                    cases[col.replace("_count", "_band")] = cases[col].map(lambda n: "Unavailable" if pd.isna(n) else str(int(n)) if n < 3 else "3+")
                events = events.join(cases[["outcome"]], on="case_id")
            cases["arrival_month"] = cases.first_start.dt.strftime("%Y-%m").fillna("Unavailable")
            events["arrival_month"] = events.start.dt.strftime("%Y-%m")
            data = dict(cases=cases, events=events, metadata=metadata, coverage=coverage,
                        source_hash=model["source_hash"], dataset=dataset.name, model_id=model_id,
                        fingerprint=f"{model['source_hash']}:{metadata['csv_sha256'] if metadata else 'no-outcomes'}")
            self.cache.clear()  # bound memory to one dataset; repeated questions reuse frames
            self.cache[key] = data
            return data


def capabilities(data):
    allowed = {}
    for table in ("cases", "events"):
        allowed[table] = [c for c in data[table].columns if c in FieldName.__args__]
    presets = []
    for label, fields in PRESETS:
        plan = AnalysisPlan(**fields)
        needed = [plan.metric, plan.group_by, plan.split_by]
        if all(c is None or c == "count" or c in allowed[plan.table] for c in needed):
            presets.append(dict(question=label, plan=plan.model_dump()))
    response = data["cases"].get("customer_response_hours")
    return dict(tables=allowed, presets=presets, case_count=len(data["cases"]), event_count=len(data["events"]),
                outcome_coverage=data["coverage"], definitions=DEFINITIONS if data["metadata"] else {},
                customer_wait=None if response is None else dict(observed=int(response.notna().sum()),
                    missing=int(response.isna().sum()), median_hours=None if not response.notna().any() else float(response.median())),
                source=data["dataset"], fingerprint=data["fingerprint"])


def execute(data, plan):
    frame = data[plan.table]
    required = [plan.metric, plan.group_by, plan.split_by, *(f.field for f in plan.filters)]
    missing = [c for c in required if c and c != "count" and c not in frame.columns]
    if missing:
        raise ValueError(f"Unavailable fields for {plan.table}: {', '.join(sorted(set(missing)))}. Choose a supported field or extract case outcomes.")
    total = len(frame)
    for condition in plan.filters:
        column = frame[condition.field]
        if condition.operator == "in":
            if pd.api.types.is_numeric_dtype(column):
                try:
                    values = [float(v) for v in condition.value]
                except (ValueError, TypeError):
                    raise ValueError(f"{condition.field} requires numeric filter values.")
                if not all(math.isfinite(v) for v in values):
                    raise ValueError("Filter values must be finite numbers.")
            else:
                values = [str(v) for v in condition.value]
            frame = frame[column.isin(values)]
            continue
        if pd.api.types.is_numeric_dtype(column):
            try:
                value = float(condition.value)
            except (ValueError, TypeError):
                raise ValueError(f"{condition.field} requires a numeric filter.")
            if not math.isfinite(value):
                raise ValueError("Filter values must be finite numbers.")
        else:
            if condition.operator != "eq" and condition.field != "arrival_month":
                raise ValueError("Use equality for categorical filters.")
            value = str(condition.value)
        mask = column.eq(value) if condition.operator == "eq" else column.ge(value) if condition.operator == "gte" else column.le(value)
        frame = frame[mask.fillna(False)]
    groups = [c for c in (plan.group_by, plan.split_by) if c]
    grouped = frame.groupby(groups, dropna=False, sort=True) if groups else [((), frame)]
    primary_counts = frame.groupby(plan.group_by, dropna=False).size() if plan.split_by else None
    rows = []
    for key, group in grouped:
        keys = key if isinstance(key, tuple) else (key,)
        values = None if plan.metric == "count" else group[plan.metric].dropna()
        n = len(group) if values is None else len(values)
        value = len(group) if values is None else None if not n else float(values.mean() if plan.aggregation == "mean" else values.median() if plan.aggregation == "median" else values.quantile(.9))
        rows.append(dict(label=" / ".join(str(k) for k in keys) if keys else "All selected records",
                         value=value, records=len(group), observed=n, missing=len(group)-n,
                         within_group_pct=float(100 * len(group) / primary_counts.loc[keys[0]]) if primary_counts is not None else None,
                         share_pct=100 * len(group) / len(frame) if len(frame) else 0))
    if plan.order != "group" and plan.chart != "line":
        rows.sort(key=lambda r: (r["value"] is None, (-1 if plan.order == "value_desc" else 1) * (r["value"] or 0)))
    count = len(rows)
    all_rows = rows
    rows = rows[:plan.limit]
    missing_count = 0 if plan.metric == "count" else int(frame[plan.metric].isna().sum())
    unit = "records" if plan.metric in {"count", "event_count"} else "days" if plan.metric == "cycle_days" else "minutes" if plan.metric == "task_minutes" else "hours" if plan.metric in {"gap_hours", "customer_response_hours"} else "count"
    valid = [r for r in all_rows if r["value"] is not None]
    headline = f"{len(frame):,} of {total:,} {plan.table} match the selected filters."
    if valid:
        highest = max(valid, key=lambda r: r["value"])
        headline += f" Highest {plan.aggregation} {plan.metric.replace('_', ' ')}: {highest['label']} ({highest['value']:,.2f} {unit}; {highest['observed']:,} observations)."
    warnings = ["Descriptive association, not evidence of causation or an individual prediction.",
                "Late-arriving cases may be cut short by the log end; outcome groups can differ in case mix."]
    if missing_count:
        warnings.append(f"{missing_count:,} matching records have no observed {plan.metric.replace('_', ' ')} and are excluded from this metric.")
    if any(r["observed"] < 30 for r in rows):
        warnings.append("Some groups have fewer than 30 observations; treat comparisons cautiously.")
    if count > len(rows):
        warnings.append(f"Showing {len(rows)} of {count} groups. Shares use all matching records, not just displayed groups.")
    if not data["metadata"] and plan.metric == "cycle_days":
        warnings.append("Cycle time uses the work-item log only; full-case outcomes have not been extracted.")
    explanation = "Use the table to check group sizes and missing observations before comparing values."
    next_step = "Choose a specific objective and test a proposed change in Scenario builder; this analysis does not rank causal solutions."
    if "rework_band" in groups:
        explanation = "Rework here means entering A_Incomplete, not simply resuming a task. Compare within each outcome before drawing conclusions: cancelled cases can remain open a long time without any recorded rework. Difficult cases can also cause both rework and delay. This chart measures associations, not the effect of removing rework."
        next_step = "Investigate the causes of incomplete applications; test a clearly stated delay-reduction assumption against the baseline."
    elif plan.metric == "customer_response_hours":
        explanation = DEFINITIONS["customer_response_hours"]
        next_step = "Test a response-delay reduction as a hypothesis. The simulator's residual delays include other causes, so do not equate a 20% response reduction with a 20% reduction of every residual delay."
    elif "resource" in groups and "outcome" in groups:
        explanation = "A resource's outcome mix reflects which cases reached them, not proof that the resource caused the outcome. Specialists and senior staff are often assigned cases already more likely to succeed; a resource touching many cases (e.g. an automated intake step) will show up in every outcome simply by being present early."
        next_step = "Look at a resource's calendar and activities in Resource profiles before assuming their outcome mix reflects performance."
    return dict(plan=plan.model_dump(), rows=rows, matching_records=len(frame), total_records=total,
                group_count=count, missing_records=missing_count, unit=unit, headline=headline,
                explanation=explanation, warnings=warnings, next_step=next_step,
                definitions=DEFINITIONS if data["metadata"] else {}, fingerprint=data["fingerprint"],
                dataset=data["dataset"], model_id=data["model_id"])


def plan_question(question, catalog, previous_plan=None):
    # Exact starter questions never require credentials or network access.
    for item in catalog["presets"]:
        if question.strip().casefold() == item["question"].casefold():
            return Intent(plan=AnalysisPlan(**item["plan"]))
    from langchain_core.messages import HumanMessage, SystemMessage
    from webapp.orchestrator.llm import build_llm
    from langsmith import tracing_context
    prompt = ("Translate a data-analysis question into the allowed typed analysis plan. Do not answer with invented results. "
              "You receive only a schema; data and computations stay local. Never generate code or SQL. "
              "For unsupported predictions, optimization, causality, unavailable columns or ambiguous requests, return no plan "
              "and a specific clarification explaining the limit. Never replace an unsupported request with an unrelated chart. "
              "Use cases for outcomes and case duration; events for activities/resources/task duration/gaps. "
              "count/count counts rows. Numeric metrics use mean/median/p90. Each plan answers ONE question. "
              "For percentages use count grouped by the requested category; the output includes shares of matching records. "
              "Use the 'in' filter with a list of values when selecting multiple outcomes or resources. "
              "Rework means A_Incomplete entries. Response time is observed offer sent-to-returned intervals. "
              "arrival_month is YYYY-MM; on events it is task-start month. Outcome labels: Pending, Denied, Cancelled, Unresolved, Unavailable. "
              "offer_band and rework_band labels: 0, 1, 2, 3+, Unavailable. "
              "Follow-up questions may modify the previous plan; preserve other settings unless the user asks otherwise. "
              "Allowed table fields: " + json.dumps(catalog["tables"]) +
              " Previous plan: " + (previous_plan.model_dump_json() if previous_plan else "none"))
    llm = build_llm(max_tokens=1400).with_structured_output(Intent, method="function_calling")
    with tracing_context(enabled=False):
        return llm.invoke([SystemMessage(content=prompt), HumanMessage(content=question)], config={"callbacks": []})


class Analyst:
    def __init__(self):
        self.data = DataCatalog()
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="analyst")
        self.slots = threading.BoundedSemaphore(2)

    def folder(self, store):
        return store.root / "analyses"

    def get(self, store, job_id):
        if not re.fullmatch(r"[a-f0-9]{32}", job_id):
            raise FileNotFoundError("Unknown analysis")
        return read_json(self.folder(store) / f"{job_id}.json")

    def catalog(self, store, model_id):
        result = capabilities(self.data.load(store, model_id))
        paths = sorted(self.folder(store).glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        result["history"] = [j for p in paths[:100] if (j := read_json(p))["model_id"] == model_id][:20]
        return result

    def submit(self, store, request):
        store.get_model(request.model_id)
        if not self.slots.acquire(blocking=False):
            raise BusyError("Two analyses are already running. Wait for one to finish.")
        job = dict(id=uuid.uuid4().hex, model_id=request.model_id, question=request.question or "Structured analysis",
                   status="running", created_at=datetime.now(timezone.utc).isoformat(), result=None, error=None)
        try:
            atomic_json(self.folder(store) / f"{job['id']}.json", job)
            self.executor.submit(self._run, store, request, job.copy())
        except Exception:
            self.slots.release()
            raise
        return job

    def _run(self, store, request, job):
        try:
            data = self.data.load(store, request.model_id)
            intent = Intent(plan=request.plan) if request.plan else plan_question(request.question, capabilities(data), request.previous_plan)
            if intent.plan is None:
                job.update(status="clarification", error=intent.clarification or "Choose a supported metric and grouping.")
            else:
                job.update(status="done", result=execute(data, intent.plan))
        except (ValueError, FileNotFoundError) as exc:
            job.update(status="error", error=str(exc))
        except Exception:
            job.update(status="error", error="The language planner or analysis service is unavailable. Retry, or use the structured controls and suggested questions, which run locally without an LLM.")
        finally:
            try:
                atomic_json(self.folder(store) / f"{job['id']}.json", job)
            finally:
                self.slots.release()

    def recover(self, store):
        for path in self.folder(store).glob("*.json"):
            job = read_json(path)
            if job["status"] == "running":
                job.update(status="error", error="The server stopped during this analysis. Please run the question again.")
                atomic_json(path, job)


analyst = Analyst()
