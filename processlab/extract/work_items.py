"""Extract the `W_` work-item log — the event log the simulator actually trains on.

Only `W_` events describe human work with a measurable duration, so this is the file that
teaches the engine how long people take and how busy they are. Application (`A_`) and offer
(`O_`) events carry no duration and are deliberately excluded here; `extract/full_log.py`
collects all three families separately, for Data Explorer's descriptive charts only.

**Timestamps are written back verbatim.** They are parsed only to order events within a case,
never to reformat them, so the output is byte-for-byte reproducible — which matters because a
learned model's id is a content hash of this file. Reformatting the timestamps would change the
hash, invalidate every cached model, and silently shift every published number.

This replaces the standalone `xes_to_csv.py --w-only` converter. It differs from that converter
in one respect: that one sorted on the raw timestamp *string*, which is only correct while every
timestamp shares one timezone and one precision. This sorts on the parsed instant instead, so a
log with mixed offsets converts correctly. On BPI 2017 the two orderings agree for all 31,509
cases, so the output is identical.
"""
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
import csv
import gzip

from lxml import etree

from ..core.storage import atomic_json, file_hash
from .lifecycle import CLOSES, OPENS

VERSION = 1
COLUMNS = ["case_id", "activity", "resource", "start_timestamp", "end_timestamp"]
PREFIX = "W_"


def _instant(raw: str) -> datetime:
    """Parse a timestamp for ordering only; the raw string is what gets written out."""
    stamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError(f"XES timestamps must include a timezone: {raw!r}")
    return stamp


def pair_case(events):
    """Pair one case's work-item lifecycle rows into intervals.

    `events` are tuples of (activity, transition, raw_timestamp, resource) in any order.
    Returns (rows, stats) where each row is
    (activity, resource, raw_start_timestamp, raw_end_timestamp).
    """
    pending = defaultdict(deque)
    rows = []
    stats = dict(unmatched_closes=0, instantaneous=0)
    for activity, transition, raw, resource in sorted(events, key=lambda e: _instant(e[2])):
        transition = (transition or "").lower()
        if transition in OPENS:
            pending[activity].append((raw, resource))
        elif transition in CLOSES:
            if pending[activity]:
                start_raw, start_resource = pending[activity].popleft()
                # The closing row's resource wins; fall back to whoever opened it.
                rows.append((activity, resource or start_resource, start_raw, raw))
            else:
                # A close with nothing open before it. Skipping beats inventing a
                # zero-length interval that would read as real work taking no time.
                stats["unmatched_closes"] += 1
        elif transition == "":
            # No lifecycle information at all: the event is a point in time, not a span.
            # BPI 2017 has none of these among W_ events; kept so other logs convert the
            # same way the original converter did.
            rows.append((activity, resource, raw, raw))
            stats["instantaneous"] += 1
        # Everything else (schedule, withdraw, ate_abort) is not a unit of work.
    stats["unclosed_opens"] = sum(len(q) for q in pending.values())
    return rows, stats


def _case_events(trace):
    """Case id plus this trace's `W_` lifecycle rows, timestamps untouched."""
    case_id, events = None, []
    for child in trace:
        if etree.QName(child).localname != "event":
            if child.get("key") == "concept:name":
                case_id = child.get("value")
            continue
        attrs = {a.get("key"): a.get("value") for a in child}
        name, raw = attrs.get("concept:name"), attrs.get("time:timestamp")
        if name and raw and name.startswith(PREFIX):
            events.append((name, attrs.get("lifecycle:transition"), raw,
                           attrs.get("org:resource")))
    return case_id, events


def extract(source: Path, destination: Path) -> dict:
    """Stream the XES at `source` into the work-item CSV at `destination`.

    Constant memory: each trace is cleared as soon as it has been written, so a 578 MB log
    converts without loading. Writes a companion `.json` recording the source and output
    hashes, so a CSV can always be traced back to the log it came from.
    """
    source, destination = Path(source), Path(destination)
    if source.resolve() == destination.resolve():
        raise ValueError("Input and output paths must differ.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".csv.tmp")
    cases = rows_out = 0
    totals = dict(unmatched_closes=0, instantaneous=0, unclosed_opens=0)
    opener = gzip.open if source.suffix == ".gz" else open
    try:
        with opener(source, "rb") as stream, temporary.open("w", encoding="utf-8", newline="") as output:
            writer = csv.writer(output)
            writer.writerow(COLUMNS)
            for _, trace in etree.iterparse(stream, events=("end",), tag="{*}trace",
                                            resolve_entities=False, no_network=True):
                case_id, events = _case_events(trace)
                if case_id and events:
                    rows, stats = pair_case(events)
                    for activity, resource, start, end in rows:
                        writer.writerow([case_id, activity, resource or "", start, end])
                    rows_out += len(rows)
                    for key in totals:
                        totals[key] += stats[key]
                    cases += 1
                trace.clear()
                while trace.getprevious() is not None:
                    del trace.getparent()[0]
        if not rows_out:
            raise ValueError("No work items found in the XES file.")
        temporary.replace(destination)
        metadata = dict(version=VERSION, source=source.name, source_sha256=file_hash(source),
                        csv_sha256=file_hash(destination), cases=cases, rows=rows_out,
                        prefix=PREFIX, opens=list(OPENS), closes=list(CLOSES), **totals,
                        extracted_at=datetime.now(timezone.utc).isoformat())
        atomic_json(destination.with_suffix(".json"), metadata)
        return metadata
    finally:
        temporary.unlink(missing_ok=True)
