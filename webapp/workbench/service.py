"""One bounded background worker; immutable model snapshots and isolated results."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import csv
import hashlib
import json
from pathlib import Path
import re
import threading
import time
import uuid

import pandas as pd

from webapp.orchestrator.config import REPO_ROOT
from webapp.orchestrator.dataset_registry import known_datasets, get_dataset
from .model import MODEL_VERSION, atomic_json, discover, file_hash, public_model
from .schemas import ExperimentRequest
from .simulation import compare, scenario_resources, simulate, validate


class BusyError(ValueError):
    pass


def read_json(path: Path):
    """Read a JSON file that another thread may be replacing (Windows denies reads mid-replace)."""
    for attempt in range(40):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except PermissionError:
            if attempt == 39:
                raise
            time.sleep(.025)


class Workbench:
    def __init__(self, root=None, raw_data=None):
        self.root = Path(root or REPO_ROOT / "runs" / "workbench")
        self.raw_data = Path(raw_data or REPO_ROOT / "raw_data")
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="scenario")
        self.slot = threading.Lock()
        self.cache = {}
        self.cache_lock = threading.Lock()

    def recover(self):
        for path in (self.root / "jobs").glob("*.json"):
            job = json.loads(path.read_text(encoding="utf-8"))
            if job["status"] in {"queued", "running"}:
                job.update(status="error", error="The server stopped before this job finished. Please run it again.")
                atomic_json(path, job)

    def datasets(self):
        result = []
        for dataset in known_datasets():
            if not dataset.user_facing:
                continue
            path = self.raw_data / dataset.relative_log_path
            if path.exists():
                result.append(dict(name=dataset.name, description=dataset.description,
                    size_mb=round(path.stat().st_size / 1024 ** 2, 1),
                    supported=dataset.name != "BPIC_2019",
                    reason="The current BPI 2019 export contains only zero-duration events." if dataset.name == "BPIC_2019" else None))
        return result

    def get_model(self, model_id):
        if not re.fullmatch(r"[a-f0-9]{24}", model_id):
            raise FileNotFoundError("Unknown model")
        with self.cache_lock:
            if model_id not in self.cache:
                model = json.loads((self.root / "models" / f"{model_id}.json").read_text(encoding="utf-8"))
                if model["version"] != MODEL_VERSION:
                    raise ValueError("This model uses an older version. Learn it again from the dataset.")
                if len(self.cache) >= 3:
                    self.cache.pop(next(iter(self.cache)))
                self.cache[model_id] = model
            return self.cache[model_id]

    def _job_path(self, job_id):
        if not re.fullmatch(r"[a-f0-9]{32}", job_id):
            raise FileNotFoundError("Unknown job")
        return self.root / "jobs" / f"{job_id}.json"

    def job(self, job_id):
        return read_json(self._job_path(job_id))

    def history(self):
        paths = sorted((self.root / "jobs").glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:50]
        return [read_json(p) for p in paths]

    def submit(self, kind, payload):
        if kind == "discover":
            if not get_dataset(payload):
                raise ValueError("Choose an available dataset.")
        else:
            model = self.get_model(payload.model_id)
            scenario_resources(model, payload)
        if not self.slot.acquire(blocking=False):
            raise BusyError("Another model or experiment is running. Wait for it to finish.")
        job_id = uuid.uuid4().hex
        job = dict(id=job_id, kind=kind, status="queued", progress=0,
                   message="Queued", created_at=datetime.now(timezone.utc).isoformat(),
                   name=payload if kind == "discover" else payload.name,
                   dataset=payload if kind == "discover" else model["dataset"], error=None)
        try:
            atomic_json(self._job_path(job_id), job)
            self.executor.submit(self._execute, job, payload)
        except Exception:
            self.slot.release()
            raise
        return job

    def _execute(self, job, payload):
        def progress(message, percent):
            job.update(status="running", message=message, progress=percent)
            atomic_json(self._job_path(job["id"]), job)
        try:
            if job["kind"] == "discover":
                progress("Reading historical data and creating a temporal train/test split", 10)
                dataset = get_dataset(payload)
                path = self.raw_data / dataset.relative_log_path
                digest = file_hash(path)
                model_id = hashlib.sha256(f"{MODEL_VERSION}:{payload}:{digest}".encode()).hexdigest()[:24]
                model_path = self.root / "models" / f"{model_id}.json"
                if not model_path.exists():
                    mapping = {dataset.case_id: "case_id", dataset.activity_name: "activity", dataset.resource: "resource",
                               dataset.start_timestamp: "start", dataset.end_timestamp: "end"}
                    frame = pd.read_csv(path, usecols=list(mapping), dtype={dataset.case_id: str, dataset.resource: str}, nrows=1_000_001).rename(columns=mapping)
                    if len(frame) > 1_000_000:
                        raise ValueError("Use a dataset with at most one million events for this workbench.")
                    full_log_path = self.raw_data / f"{dataset.name}.full_log.csv"
                    full_events = None
                    if full_log_path.exists():
                        progress("Reading the full application/offer/work-item log for Data Explorer", 20)
                        # Descriptive only (see full_log.py) — a separate, more generous cap than the
                        # simulation-critical W_ CSV above, since it never feeds resource capacity.
                        full_events = pd.read_csv(full_log_path, dtype={"case_id": str, "resource": str}, nrows=5_000_001)
                        if len(full_events) > 5_000_000:
                            raise ValueError("The full event log exceeds five million events.")
                    progress("Learning resource schedules, durations, handoffs and case patterns", 35)
                    model = discover(frame, payload, model_id, digest, full_events=full_events)
                    atomic_json(model_path, model)
                model = self.get_model(model_id)
                job["model_id"] = model_id
                job["message"] = f"Learned {len(model['resources'])} resource profiles from {model['training_cases']:,} training cases."
            else:
                model = self.get_model(payload.model_id)
                output = self.root / "experiments" / job["id"]
                output.mkdir(parents=True, exist_ok=False)
                atomic_json(output / "request.json", payload.model_dump())
                before, after = [], []
                deadline = time.monotonic() + 900
                for i in range(payload.repetitions):
                    seed = payload.seed + i
                    for label, records in [("baseline", before), ("scenario", after)]:
                        percent = 5 + int(90 * (2 * i + (label == "scenario")) / (2 * payload.repetitions))
                        progress(f"Running {label}, repetition {i + 1} of {payload.repetitions}", percent)
                        run, events = simulate(model, payload, seed, baseline=label == "baseline", deadline=deadline)
                        records.append(run)
                        with (output / f"{label}_{i + 1}.csv").open("w", newline="", encoding="utf-8") as stream:
                            writer = csv.DictWriter(stream, fieldnames=list(events[0]))
                            writer.writeheader()
                            for event in events:
                                for key in ["arrival_timestamp", "ready_timestamp", "start_timestamp", "end_timestamp"]:
                                    event[key] = datetime.fromtimestamp(event[key], tz=timezone.utc).isoformat()
                                writer.writerow(event)
                result = dict(id=job["id"], request=payload.model_dump(), dataset=model["dataset"],
                    model_id=model["model_id"], model_version=model["version"], source_hash=model["source_hash"],
                    assumptions=model["assumptions"], historical_cycle_hours=model["historical_cycle_hours"],
                    completed_at=datetime.now(timezone.utc).isoformat(), baseline_runs=before, scenario_runs=after,
                    comparison=compare(before, after), validation=validate(model, before, payload.horizon_days))
                atomic_json(output / "result.json", result)
                job.update(model_id=model["model_id"], message="Baseline and scenario comparison ready")
            job.update(status="done", progress=100)
        except Exception as exc:
            job.update(status="error", error=str(exc), message="Could not complete this job")
        finally:
            job["finished_at"] = datetime.now(timezone.utc).isoformat()
            try:
                atomic_json(self._job_path(job["id"]), job)
            finally:
                self.slot.release()

    def result(self, job_id):
        job = self.job(job_id)
        if job["kind"] != "experiment" or job["status"] != "done":
            raise ValueError("This experiment has not completed successfully.")
        return read_json(self.root / "experiments" / job_id / "result.json")

    def download(self, job_id, filename):
        result = self.result(job_id)
        match = re.fullmatch(r"(baseline|scenario)_(\d+)\.csv", filename)
        if filename not in {"result.json", "request.json"} and not (match and 1 <= int(match[2]) <= result["request"]["repetitions"]):
            raise FileNotFoundError("Unknown experiment artifact")
        path = self.root / "experiments" / job_id / filename
        if not path.is_file():
            raise FileNotFoundError("This artifact is no longer present on disk.")
        return path


workbench = Workbench()
