"""Known event logs and their column mappings.

Column names come from run_commands.sh (the paper's canonical invocations) plus the verified
header of BPIC_2019.csv. This is a convenience lookup, not a trust boundary: guardrails.py always
re-validates the actual header before a run, whether or not the dataset is registered here.
"""
from dataclasses import dataclass

from .config import get_settings


@dataclass(frozen=True)
class DatasetEntry:
    name: str
    relative_log_path: str  # relative to raw_data/
    case_id: str
    activity_name: str
    resource: str
    start_timestamp: str
    end_timestamp: str
    description: str
    user_facing: bool = True  # False: kept only as a fast, deterministic test fixture; hidden from the product


_REGISTRY: dict[str, DatasetEntry] = {
    e.name: e
    for e in [
        DatasetEntry(
            name="LoanApp",
            relative_log_path="LoanApp.csv.gz",
            case_id="case_id",
            activity_name="activity",
            resource="resource",
            start_timestamp="start_time",
            end_timestamp="end_time",
            description="Small synthetic loan-application process log. Fastest dataset to simulate.",
            user_facing=False,  # synthetic data; kept only so tests have a ~2-minute end-to-end run
        ),
        DatasetEntry(
            name="BPIC_2017_W",
            relative_log_path="BPIC_2017_W.csv",
            case_id="case_id",
            activity_name="activity",
            resource="resource",
            start_timestamp="start_timestamp",
            end_timestamp="end_timestamp",
            description="BPI Challenge 2017, work-item subset. Large loan-application process log.",
        ),
        DatasetEntry(
            name="BPIC_2019",
            relative_log_path="BPIC_2019.csv",
            case_id="case_id",
            activity_name="activity",
            resource="resource",
            start_timestamp="start_timestamp",
            end_timestamp="end_timestamp",
            description="BPI Challenge 2019 purchase-order process log. Very large.",
        ),
    ]
}


def known_datasets() -> list[DatasetEntry]:
    """All registered datasets — includes internal test fixtures. Use for validation, not for
    anything a user sees; user-facing listings should filter to `user_facing`."""
    return list(_REGISTRY.values())


def get_dataset(name: str) -> DatasetEntry | None:
    return _REGISTRY.get(name)


def available_datasets() -> list[DatasetEntry]:
    """User-facing registry entries whose backing file actually exists on disk right now."""
    settings = get_settings()
    return [
        e for e in _REGISTRY.values()
        if e.user_facing and (settings.raw_data_dir / e.relative_log_path).exists()
    ]
