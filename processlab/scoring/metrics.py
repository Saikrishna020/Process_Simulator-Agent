"""Score a simulated log against the real held-out log, with the five published distance
measures used by the AgentSimulator paper (Kirchdorfer et al., arXiv 2408.08571).

All five come from the `log-distance-measures` package — they are *not* this project's own
metrics, which is the point: they are the standard yardstick, so a number here is comparable
to published work rather than self-defined.

Lower is better for every measure.

## Read these caveats before quoting any number

This engine **resamples frozen real training traces**. That makes two of the five measures
much less informative than they look, and saying so is part of reporting them honestly:

* **NGD is structurally flattered.** A 3-gram comparison of activity sequences largely measures
  how much the *training* traces resemble the *test* traces — i.e. control-flow drift in the
  data — not the model's skill, because the simulator emits real observed sequences by
  construction. A good NGD here is close to a tautology. Report it; never claim it as a
  modelling win.
* **AEDD depends on horizon alignment.** The engine simulates a *sampled* arrival schedule from
  the start of the held-out period rather than replaying the actual test cases, so absolute
  calendar placement is sensitive to the window. `holdout_log()` therefore trims the real log to
  the same arrival window the simulation covers, so the two populations are comparable by
  construction — but AEDD still answers "is the clock roughly right", not "did we predict this
  case".
* **CEDD, REDD and CTDD are the informative three** for this engine: hour-of-week rhythm, timing
  relative to each case's start, and overall case duration. CTDD is the one that reflects
  queueing and delay — the thing the model is actually for.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from ..core.config import get_settings
from ..core.datasets import get_dataset
from ..core.storage import file_hash
from ..engine.calendar import DAY
from ..engine.split import temporal_split, validate_event_log

MAX_SCORING_HORIZON_DAYS = 90  # matches ExperimentRequest's cap

METRICS = {
    "NGD": dict(
        name="N-Gram Distance (n=3)",
        question="Are the sequences of activities realistic?",
        informative=False,
        note="Structurally flattered: the engine replays real observed traces, so this largely "
             "measures train-vs-test control-flow drift, not model skill.",
    ),
    "AEDD": dict(
        name="Absolute Event Distribution Distance",
        question="Do events land at the right absolute calendar times?",
        informative=False,
        note="Sensitive to arrival-horizon alignment; the real log is trimmed to the simulated "
             "window to make this comparable at all.",
    ),
    "CEDD": dict(
        name="Circadian Event Distribution Distance",
        question="Is the hour-of-day / day-of-week rhythm right?",
        informative=True,
        note="Informative: tests the inferred resource calendars.",
    ),
    "REDD": dict(
        name="Relative Event Distribution Distance",
        question="Is event timing right relative to each case's start?",
        informative=True,
        note="Informative: tests how waiting is distributed through a case.",
    ),
    "CTDD": dict(
        name="Cycle Time Distribution Distance",
        question="Are overall case durations right?",
        informative=True,
        note="The headline measure: reflects queueing and residual delay together.",
    ),
}


def _event_log_ids():
    from log_distance_measures.config import EventLogIDs

    return EventLogIDs(
        case="case_id", activity="activity",
        start_time="start_time", end_time="end_time", resource="resource",
    )


def align(df: pd.DataFrame) -> pd.DataFrame:
    """Map either log shape onto the measures' expected column names.

    Both the engine's event CSV and the source event log already use `case_id`/`activity`/
    `resource`; only the timestamp columns differ in name.
    """
    df = df.rename(columns={
        "start_timestamp": "start_time", "end_timestamp": "end_time",
        "start": "start_time", "end": "end_time",
    })
    missing = {"case_id", "activity", "resource", "start_time", "end_time"} - set(df.columns)
    if missing:
        raise ValueError(f"Log is missing required columns: {', '.join(sorted(missing))}")
    for col in ("start_time", "end_time"):
        df[col] = pd.to_datetime(df[col], utc=True, format="mixed")
    return df


@dataclass
class Holdout:
    events: pd.DataFrame
    cases: int
    horizon_days: int
    arrival_start: pd.Timestamp
    arrival_end: pd.Timestamp
    trimmed_from_cases: int


def holdout_log(model: dict, raw_data_dir: Path | None = None) -> Holdout:
    """Rebuild the **real** held-out event log this model never saw.

    Uses engine.split.temporal_split — the same function discover() used — so the split cannot
    drift from the one the model was trained against. The log is then trimmed to the arrival
    window a scoring simulation actually covers (see the AEDD caveat in the module docstring).
    """
    dataset = get_dataset(model["dataset"])
    if not dataset:
        raise ValueError(f"Dataset {model['dataset']!r} is not registered.")
    raw_data_dir = raw_data_dir or get_settings().raw_data_dir
    source = Path(raw_data_dir) / dataset.relative_log_path
    if not source.exists():
        raise FileNotFoundError(f"Source log not found: {source}")
    if file_hash(source) != model["source_hash"]:
        raise ValueError(
            "The source log changed since this model was learned. Learn it again before scoring."
        )

    mapping = {dataset.case_id: "case_id", dataset.activity_name: "activity",
               dataset.resource: "resource", dataset.start_timestamp: "start",
               dataset.end_timestamp: "end"}
    frame = pd.read_csv(source, usecols=list(mapping),
                        dtype={dataset.case_id: str, dataset.resource: str}).rename(columns=mapping)
    df = validate_event_log(frame)
    split = temporal_split(df)

    test_bounds = split.bounds.loc[split.test_ids]
    arrival_start = test_bounds.start.min()
    span_days = int((test_bounds.start.max() - arrival_start).days) + 1
    horizon_days = min(MAX_SCORING_HORIZON_DAYS, span_days)
    arrival_end = arrival_start + pd.Timedelta(days=horizon_days)

    kept = test_bounds.index[(test_bounds.start >= arrival_start) & (test_bounds.start < arrival_end)]
    events = split.holdout_events(df)
    events = events[events.case_id.isin(kept)]
    return Holdout(
        events=align(events), cases=len(kept), horizon_days=horizon_days,
        arrival_start=arrival_start, arrival_end=arrival_end,
        trimmed_from_cases=len(test_bounds),
    )


def score_logs(real: pd.DataFrame, simulated: list[pd.DataFrame]) -> dict:
    """The five measures for each simulated log against one real log."""
    from log_distance_measures.absolute_event_distribution import absolute_event_distribution_distance
    from log_distance_measures.circadian_event_distribution import circadian_event_distribution_distance
    from log_distance_measures.config import AbsoluteTimestampType, discretize_to_hour
    from log_distance_measures.cycle_time_distribution import cycle_time_distribution_distance
    from log_distance_measures.n_gram_distribution import n_gram_distribution_distance
    from log_distance_measures.relative_event_distribution import relative_event_distribution_distance

    if not simulated:
        raise ValueError("No simulated logs to score.")
    ids = _event_log_ids()
    real = align(real.copy())
    per_run: list[dict] = []
    for raw in simulated:
        sim = align(raw.copy())
        per_run.append({
            "NGD": n_gram_distribution_distance(real, ids, sim, ids, n=3),
            "AEDD": absolute_event_distribution_distance(
                real, ids, sim, ids,
                discretize_type=AbsoluteTimestampType.BOTH, discretize_event=discretize_to_hour),
            "CEDD": circadian_event_distribution_distance(
                real, ids, sim, ids, discretize_type=AbsoluteTimestampType.BOTH),
            "REDD": relative_event_distribution_distance(
                real, ids, sim, ids,
                discretize_type=AbsoluteTimestampType.BOTH, discretize_event=discretize_to_hour),
            "CTDD": cycle_time_distribution_distance(
                real, ids, sim, ids, bin_size=pd.Timedelta(hours=1)),
        })
    summary = {
        code: {
            "mean": float(np.mean([r[code] for r in per_run])),
            "std": float(np.std([r[code] for r in per_run])),
            "runs": [float(r[code]) for r in per_run],
            **{k: v for k, v in METRICS[code].items()},
        }
        for code in METRICS
    }
    return {"metrics": summary, "runs": len(per_run)}


def score_model(model: dict, repetitions: int = 3, seed: int = 42,
                raw_data_dir: Path | None = None) -> dict:
    """Run unchanged baselines over the held-out arrival window and score them.

    The horizon comes from the held-out data, **not** from whatever the user last set in
    Scenario builder: scoring is only meaningful over the window the real log covers.
    """
    from ..engine.schemas import ExperimentRequest
    from ..engine.simulation import simulate

    holdout = holdout_log(model, raw_data_dir=raw_data_dir)
    request = ExperimentRequest(
        model_id=model["model_id"], name="scoring baseline",
        horizon_days=holdout.horizon_days, repetitions=repetitions, seed=seed,
    )
    simulated = []
    for index in range(repetitions):
        _, events = simulate(model, request, seed + index, baseline=True)
        frame = pd.DataFrame(events)[
            ["case_id", "activity", "resource", "start_timestamp", "end_timestamp"]]
        for col in ("start_timestamp", "end_timestamp"):
            frame[col] = pd.to_datetime(frame[col], unit="s", utc=True)
        simulated.append(frame)

    result = score_logs(holdout.events, simulated)
    result["reference"] = {
        "dataset": model["dataset"],
        "model_id": model["model_id"],
        "source_hash": model["source_hash"],
        "real_cases": holdout.cases,
        "real_events": int(len(holdout.events)),
        "held_out_cases_total": holdout.trimmed_from_cases,
        "horizon_days": holdout.horizon_days,
        "arrival_window": [holdout.arrival_start.isoformat(), holdout.arrival_end.isoformat()],
        "seed": seed,
    }
    result["caveats"] = [
        "NGD is structurally flattered: the engine replays real observed traces, so it largely "
        "measures train-vs-test control-flow drift rather than model skill.",
        "AEDD depends on arrival-horizon alignment; the real log is trimmed to the simulated "
        f"window ({holdout.cases:,} of {holdout.trimmed_from_cases:,} held-out cases) so the two "
        "populations are comparable.",
        "CEDD, REDD and CTDD are the informative three for this engine; CTDD is the headline, "
        "since it reflects queueing and residual delay together.",
        "These are distances against held-out history, not a forecast. Use them to compare model "
        "versions, not to predict case durations.",
    ]
    return result
