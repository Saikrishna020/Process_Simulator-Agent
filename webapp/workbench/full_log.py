"""Extract the whole BPI 2017 event log — application (A_*) and offer (O_*) events alongside
the work items (W_*) — for Data Explorer's descriptive charts only.

The simulator's resource-capacity model (webapp/workbench/model.py: discover()) continues to
read only the W_ work-item CSV unchanged: A_/O_ events carry no observed duration (a single
'complete' transition, never a 'start'), so they cannot teach processing time or calendars.
Mixing them into the simulation model would silently corrupt it. This extraction exists purely
to let the "what does the history say" overview describe the whole process BPI 2017 asks about
(applications and offers, not just human work items), matching webapp/workbench/outcomes.py's
separate, explicit, non-destructive extraction for case outcomes.
"""
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
import csv
import gzip

from lxml import etree

from .model import atomic_json, file_hash

VERSION = 1


def _events(trace):
    case_id = None
    events = []
    for child in trace:
        if etree.QName(child).localname != "event":
            if child.get("key") == "concept:name":
                case_id = child.get("value")
            continue
        attrs = {a.get("key"): a.get("value") for a in child}
        name, ts, resource = attrs.get("concept:name"), attrs.get("time:timestamp"), attrs.get("org:resource")
        if name and ts:
            stamp = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                raise ValueError("XES timestamps must include a timezone.")
            transition = (attrs.get("lifecycle:transition") or "").lower()
            events.append((name, transition, stamp.astimezone(timezone.utc), resource))
    return case_id, events


def _intervals(events):
    """W_ work items carry start/complete (or resume/suspend) lifecycles with a real duration.
    Every other activity (A_*, O_*, ...) is recorded as a single 'complete' event with no
    matching start and has no observed duration — reported as instantaneous (start == end),
    the same convention xes_to_csv.py uses for lifecycle-less events."""
    pending = defaultdict(deque)
    rows = []
    for name, transition, ts, resource in sorted(events, key=lambda e: e[2]):
        if not name.startswith("W_"):
            rows.append((name, resource, ts, ts))
        elif transition in ("start", "resume"):
            pending[name].append((ts, resource))
        elif transition in ("complete", "suspend"):
            if pending[name]:
                start_ts, start_resource = pending[name].popleft()
                rows.append((name, resource or start_resource, start_ts, ts))
            # a complete/suspend with no matching start is a stray lifecycle event; skip it
            # rather than fabricate a zero-length row.
        # 'schedule', 'withdraw', 'ate_abort' and any other transition are not work intervals.
    return rows


def extract(source: Path, destination: Path):
    source, destination = Path(source), Path(destination)
    if source.resolve() == destination.resolve():
        raise ValueError("Input and output paths must differ.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".csv.tmp")
    cases = events_out = 0
    opener = gzip.open if source.suffix == ".gz" else open
    try:
        with opener(source, "rb") as stream, temporary.open("w", encoding="utf-8", newline="") as output:
            writer = csv.writer(output)
            writer.writerow(["case_id", "activity", "resource", "start", "end"])
            for _, trace in etree.iterparse(stream, events=("end",), tag="{*}trace", resolve_entities=False, no_network=True):
                case_id, events = _events(trace)
                if case_id and events:
                    rows = _intervals(events)
                    for name, resource, start, end in rows:
                        writer.writerow([case_id, name, resource or "", start.isoformat(), end.isoformat()])
                    events_out += len(rows)
                    cases += 1
                trace.clear()
                while trace.getprevious() is not None:
                    del trace.getparent()[0]
        if not cases:
            raise ValueError("No cases found in the XES file.")
        temporary.replace(destination)
        metadata = dict(version=VERSION, source=source.name, source_sha256=file_hash(source),
                        csv_sha256=file_hash(destination), cases=cases, events=events_out,
                        extracted_at=datetime.now(timezone.utc).isoformat())
        atomic_json(destination.with_suffix(".json"), metadata)
        return metadata
    finally:
        temporary.unlink(missing_ok=True)
