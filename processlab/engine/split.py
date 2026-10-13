"""The temporal train/test split — defined exactly once.

Two consumers depend on this being identical: engine.model.discover() learns from the training
cases, and scoring.metrics rebuilds the *real held-out event log* to score simulated output
against. If scoring derived its own split, every number it produced would be quietly invalid,
so the logic lives here and neither module reimplements it.

The rule (unchanged from the original model, MODEL_VERSION 4):
  * cutoff   = the 80th-percentile case *start* time
  * training = cases that **finish** before the cutoff      (fully observed)
  * held-out = cases that **start** at or after the cutoff  (unseen)
  * cases straddling the cutoff belong to neither and are excluded — a half-observed case
    would teach wrong durations and a wrong ending.
"""
from dataclasses import dataclass

import pandas as pd

REQUIRED_COLUMNS = ["case_id", "activity", "resource", "start", "end"]


def clean_events(frame: pd.DataFrame, required: list | None = None) -> pd.DataFrame:
    """Coerce the five required columns to their model types. Unparseable timestamps become NaT."""
    required = required or REQUIRED_COLUMNS
    df = frame[required].copy()
    for col in ["case_id", "activity", "resource"]:
        df[col] = df[col].astype(str)
    for col in ["start", "end"]:
        df[col] = pd.to_datetime(df[col], utc=True, format="mixed", errors="coerce")
    return df


def validate_event_log(frame: pd.DataFrame) -> pd.DataFrame:
    """Reject logs that cannot teach resource capacity. Returns the cleaned frame."""
    if any(c not in frame for c in REQUIRED_COLUMNS):
        raise ValueError("The log requires case ID, activity, resource, start and end columns.")
    df = frame[REQUIRED_COLUMNS].copy()
    if df[["case_id", "activity", "resource"]].isna().any().any():
        raise ValueError("The log contains missing case, activity or resource identifiers.")
    df = clean_events(df)
    if df[["start", "end"]].isna().any().any() or (df.end < df.start).any():
        raise ValueError("The log contains invalid timestamps or negative durations.")
    if float((df.end == df.start).mean()) == 1:
        raise ValueError(
            "All events have zero duration. Supply measured start/end times before "
            "modeling resource capacity."
        )
    return df


def case_bounds(df: pd.DataFrame) -> pd.DataFrame:
    """First start and last end per case, ordered by arrival."""
    return df.groupby("case_id").agg(start=("start", "min"), end=("end", "max")).sort_values("start")


@dataclass(frozen=True)
class Split:
    bounds: pd.DataFrame
    cutoff: pd.Timestamp
    train_ids: pd.Index
    test_ids: pd.Index

    @property
    def excluded_boundary_cases(self) -> int:
        return len(self.bounds) - len(self.train_ids) - len(self.test_ids)

    def train_events(self, df: pd.DataFrame) -> pd.DataFrame:
        return df[df.case_id.isin(self.train_ids)].sort_values(["case_id", "start", "end"]).copy()

    def holdout_events(self, df: pd.DataFrame) -> pd.DataFrame:
        """The real, unseen event log — what a simulated log is scored against."""
        return df[df.case_id.isin(self.test_ids)].sort_values(["case_id", "start", "end"]).copy()


def temporal_split(df: pd.DataFrame, bounds: pd.DataFrame | None = None) -> Split:
    bounds = case_bounds(df) if bounds is None else bounds
    if len(bounds) < 10:
        raise ValueError("At least ten complete historical cases are required.")
    cutoff = bounds.iloc[min(len(bounds) - 1, int(len(bounds) * .8))].start
    train_ids = bounds.index[bounds.end < cutoff]
    test_ids = bounds.index[bounds.start >= cutoff]
    if len(train_ids) < 5 or not len(test_ids):
        raise ValueError("The temporal split has too few complete cases. Supply a longer history.")
    return Split(bounds=bounds, cutoff=cutoff, train_ids=train_ids, test_ids=test_ids)
