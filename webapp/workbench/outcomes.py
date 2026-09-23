"""Streaming BPI 2017 case facts. Definitions are explicit, not inferred by an LLM."""
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import csv
import gzip

from lxml import etree

from .model import atomic_json, file_hash

VERSION = 1
DEFINITIONS = {
    "outcome": "Last observed A_Pending, A_Denied or A_Cancelled state; otherwise Unresolved. Pending is not proof of disbursement.",
    "cycle_days": "Elapsed days from first to last recorded event in the full case; not necessarily business completion.",
    "rework_count": "Number of A_Incomplete state entries. Lifecycle work-item resumes are not counted as rework.",
    "offer_count": "Distinct OfferID values at O_Create Offer/O_Created; fallback to creation events if IDs are absent.",
    "customer_response_hours": "Union of observed sent-to-returned intervals matched by OfferID, in hours. Overlapping offers count once. Missing/unreturned offers are excluded, not assigned zero; this is a response-time proxy, not a causal attribution.",
}


def case_outcome(case_id, events):
    events = sorted(events, key=lambda e: e["timestamp"])
    states = {"A_Pending": "Pending", "A_Denied": "Denied", "A_Cancelled": "Cancelled"}
    terminal = [e for e in events if e["activity"] in states]
    creates = [e for e in events if e["activity"] in {"O_Create Offer", "O_Created"}]
    ids = {e.get("OfferID") for e in creates if e.get("OfferID")}
    no_id = Counter(e["activity"] for e in creates if not e.get("OfferID"))
    sent, intervals = {}, []
    for e in events:
        offer = e.get("OfferID")
        if not offer:
            continue
        if e["activity"].startswith("O_Sent"):
            sent.setdefault(offer, e["timestamp"])
        elif e["activity"] == "O_Returned" and offer in sent:
            intervals.append((sent.pop(offer), e["timestamp"]))
    merged = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(end, merged[-1][1])
        else:
            merged.append([start, end])
    response = sum((end - start).total_seconds() for start, end in merged) / 3600 if merged else None
    return dict(case_id=case_id, outcome=states[terminal[-1]["activity"]] if terminal else "Unresolved",
                cycle_days=(events[-1]["timestamp"] - events[0]["timestamp"]).total_seconds() / 86400,
                rework_count=sum(e["activity"] == "A_Incomplete" for e in events),
                offer_count=len(ids) + max(no_id.values(), default=0), customer_response_hours=response,
                response_pairs=len(intervals), unreturned_offer_count=len(sent),
                first_start=events[0]["timestamp"].isoformat(), last_end=events[-1]["timestamp"].isoformat())


def extract(source: Path, destination: Path):
    source, destination = Path(source), Path(destination)
    if source.resolve() == destination.resolve():
        raise ValueError("Input and output paths must differ.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".csv.tmp")
    count = 0
    opener = gzip.open if source.suffix == ".gz" else open
    try:
        with opener(source, "rb") as stream, temporary.open("w", encoding="utf-8", newline="") as output:
            writer = None
            for _, trace in etree.iterparse(stream, events=("end",), tag="{*}trace", resolve_entities=False, no_network=True):
                case_id, events = None, []
                for child in trace:
                    if etree.QName(child).localname != "event":
                        if child.get("key") == "concept:name":
                            case_id = child.get("value")
                        continue
                    attrs = {a.get("key"): a.get("value") for a in child}
                    if attrs.get("concept:name") and attrs.get("time:timestamp"):
                        stamp = datetime.fromisoformat(attrs["time:timestamp"].replace("Z", "+00:00"))
                        if stamp.tzinfo is None:
                            raise ValueError("XES timestamps must include a timezone.")
                        events.append(dict(activity=attrs["concept:name"], timestamp=stamp.astimezone(timezone.utc), OfferID=attrs.get("OfferID")))
                if case_id and events:
                    row = case_outcome(case_id, events)
                    if writer is None:
                        writer = csv.DictWriter(output, fieldnames=list(row))
                        writer.writeheader()
                    writer.writerow(row)
                    count += 1
                trace.clear()
                while trace.getprevious() is not None:
                    del trace.getparent()[0]
        if not count:
            raise ValueError("No cases found in the XES file.")
        temporary.replace(destination)
        metadata = dict(version=VERSION, source=source.name, source_sha256=file_hash(source),
                        csv_sha256=file_hash(destination), cases=count, definitions=DEFINITIONS,
                        extracted_at=datetime.now(timezone.utc).isoformat())
        atomic_json(destination.with_suffix(".json"), metadata)
        return metadata
    finally:
        temporary.unlink(missing_ok=True)
