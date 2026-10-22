"""The W_ work-item extraction: the CSV the simulator trains on.

A learned model's id is a content hash of this CSV, so the extraction has to be reproducible
to the byte. These tests pin the pairing rule, the verbatim timestamps, and the shared
lifecycle vocabulary.
"""
import csv
from pathlib import Path

import pytest

from processlab.core.datasets import get_dataset
from processlab.core.storage import file_hash, read_json
from processlab.extract import full_log, lifecycle, work_items
from processlab.extract.work_items import COLUMNS, extract, pair_case

TS = "2016-01-02T{}Z"
REPO_ROOT = Path(__file__).resolve().parents[1]
RAW_XES = REPO_ROOT / "raw_data" / "bpi2017" / "BPI_Challenge_2017.xes"
SHIPPED_CSV = REPO_ROOT / "raw_data" / "BPIC_2017_W.csv"


def ev(activity, transition, hhmmss, resource=None):
    return (activity, transition, TS.format(hhmmss), resource)


# --- the pairing rule -------------------------------------------------------------------

def test_a_start_and_complete_become_one_interval():
    rows, stats = pair_case([
        ev("W_Complete application", "start", "11:32:57.000", "User_19"),
        ev("W_Complete application", "complete", "11:43:56.000", "User_19"),
    ])
    assert rows == [("W_Complete application", "User_19",
                     TS.format("11:32:57.000"), TS.format("11:43:56.000"))]
    assert stats["unmatched_closes"] == 0 and stats["unclosed_opens"] == 0


def test_suspend_closes_an_interval_just_like_complete():
    """W_ items end in suspend five times more often than in complete; dropping those would
    discard most of the measured work in BPI 2017."""
    rows, _ = pair_case([
        ev("W_Call after offers", "start", "11:43:56.000", "User_19"),
        ev("W_Call after offers", "suspend", "11:46:13.000", "User_19"),
    ])
    assert len(rows) == 1 and rows[0][3] == TS.format("11:46:13.000")


def test_resume_opens_a_second_interval_for_the_same_activity():
    """One activity worked twice, days apart, by two people is two rows — not one span."""
    rows, _ = pair_case([
        ev("W_Call after offers", "start", "11:43:56.000", "User_19"),
        ev("W_Call after offers", "suspend", "11:46:13.000", "User_19"),
        ev("W_Call after offers", "resume", "12:23:16.000", "User_11"),
        ev("W_Call after offers", "suspend", "12:23:42.000", "User_11"),
    ])
    assert len(rows) == 2
    assert [r[1] for r in rows] == ["User_19", "User_11"]
    assert rows[0][2] == TS.format("11:43:56.000") and rows[1][2] == TS.format("12:23:16.000")


@pytest.mark.parametrize("transition", lifecycle.IGNORED)
def test_queued_cancelled_and_abandoned_rows_produce_no_work(transition):
    rows, _ = pair_case([ev("W_Handle leads", transition, "13:00:04.000", "User_1")])
    assert rows == []


def test_a_close_with_nothing_open_is_skipped_not_fabricated():
    rows, stats = pair_case([ev("W_Call after offers", "suspend", "11:46:13.000", "User_19")])
    assert rows == []
    assert stats["unmatched_closes"] == 1


def test_an_unclosed_open_is_counted_and_emits_no_row():
    rows, stats = pair_case([ev("W_Validate application", "start", "09:00:00.000", "User_3")])
    assert rows == []
    assert stats["unclosed_opens"] == 1


def test_the_closing_resource_wins_and_falls_back_to_the_opener():
    rows, _ = pair_case([
        ev("W_Validate application", "start", "09:00:00.000", "User_3"),
        ev("W_Validate application", "complete", "09:05:00.000", None),
    ])
    assert rows[0][1] == "User_3"


def test_pairing_is_fifo_when_two_intervals_overlap():
    """15% of real work items overlap. The earliest unclosed open matches the next close."""
    rows, _ = pair_case([
        ev("W_Validate application", "start", "09:00:00.000", "A"),
        ev("W_Validate application", "start", "09:01:00.000", "B"),
        ev("W_Validate application", "complete", "09:02:00.000", "A"),
        ev("W_Validate application", "complete", "09:03:00.000", "B"),
    ])
    assert [(r[2], r[3]) for r in rows] == [
        (TS.format("09:00:00.000"), TS.format("09:02:00.000")),
        (TS.format("09:01:00.000"), TS.format("09:03:00.000")),
    ]


def test_events_are_ordered_by_instant_not_by_string():
    """Sorting raw timestamp strings breaks as soon as a log mixes timezone offsets.

    Here the start is 07:00 UTC and the close 08:00 UTC, so they pair into one hour of work.
    Sorted as strings, '…T08:00:00.000Z' sorts before '…T09:00:00.000+02:00', putting the
    close first and losing the interval entirely.
    """
    start, close = "2016-01-02T09:00:00.000+02:00", "2016-01-02T08:00:00.000Z"
    assert sorted([start, close])[0] == close, "string order really does disagree here"
    rows, stats = pair_case([
        ("W_Validate application", "complete", close, "A"),
        ("W_Validate application", "start", start, "A"),
    ])
    assert len(rows) == 1 and stats["unmatched_closes"] == 0
    assert rows[0][2] == start and rows[0][3] == close


def test_a_timestamp_without_a_timezone_is_rejected():
    with pytest.raises(ValueError, match="timezone"):
        pair_case([("W_Validate application", "start", "2016-01-02T08:00:00.000", "A")])


# --- reproducibility --------------------------------------------------------------------

def test_timestamps_are_written_back_verbatim():
    """A model id is a hash of this CSV; reformatting timestamps would invalidate every
    cached model and silently shift every published number."""
    raw_start, raw_end = "2016-01-02T10:45:22.429Z", "2016-01-02T10:49:28.816Z"
    rows, _ = pair_case([
        ("W_Complete application", "start", raw_start, "User_17"),
        ("W_Complete application", "complete", raw_end, "User_17"),
    ])
    assert rows[0][2] == raw_start and rows[0][3] == raw_end


def test_extraction_is_byte_identical_when_run_twice(tmp_path):
    xes = tmp_path / "log.xes"
    xes.write_text(SAMPLE_XES, encoding="utf-8")
    first, second = tmp_path / "a.csv", tmp_path / "b.csv"
    meta = extract(xes, first)
    extract(xes, second)
    assert first.read_bytes() == second.read_bytes()
    assert meta["csv_sha256"] == file_hash(second)
    assert meta["rows"] == 2 and meta["cases"] == 1


def test_extraction_records_its_provenance(tmp_path):
    xes = tmp_path / "log.xes"
    xes.write_text(SAMPLE_XES, encoding="utf-8")
    out = tmp_path / "w.csv"
    meta = extract(xes, out)
    saved = read_json(out.with_suffix(".json"))
    assert saved == meta
    assert saved["source_sha256"] == file_hash(xes)
    assert saved["opens"] == list(lifecycle.OPENS)
    assert saved["closes"] == list(lifecycle.CLOSES)


def test_output_has_the_columns_the_dataset_registry_expects(tmp_path):
    xes = tmp_path / "log.xes"
    xes.write_text(SAMPLE_XES, encoding="utf-8")
    out = tmp_path / "w.csv"
    extract(xes, out)
    with out.open(newline="", encoding="utf-8") as stream:
        reader = csv.reader(stream)
        assert next(reader) == COLUMNS
        rows = list(reader)
    assert [r[1] for r in rows] == ["W_Complete application", "W_Call after offers"]
    assert rows[0][0] == "Application_1"
    # application and offer events never reach the simulator's log
    assert not any(r[1].startswith(("A_", "O_")) for r in rows)


def test_input_and_output_must_differ(tmp_path):
    xes = tmp_path / "log.xes"
    xes.write_text(SAMPLE_XES, encoding="utf-8")
    with pytest.raises(ValueError, match="must differ"):
        extract(xes, xes)


# --- the two extractors must not drift ---------------------------------------------------

def test_both_extractors_share_one_lifecycle_vocabulary():
    """full_log.py and work_items.py apply the same pairing rule. If someone adds a
    transition to one, this fails rather than letting the two quietly disagree."""
    assert full_log.OPENS is lifecycle.OPENS
    assert full_log.CLOSES is lifecycle.CLOSES
    assert work_items.OPENS is lifecycle.OPENS
    assert work_items.CLOSES is lifecycle.CLOSES
    assert not set(lifecycle.OPENS) & set(lifecycle.CLOSES)
    assert not set(lifecycle.IGNORED) & (set(lifecycle.OPENS) | set(lifecycle.CLOSES))


def test_the_two_extractors_agree_on_work_item_intervals(tmp_path):
    """Given the same case, both must derive the same W_ intervals; only the treatment of
    A_/O_ events and the timestamp formatting differ."""
    xes = tmp_path / "log.xes"
    xes.write_text(SAMPLE_XES, encoding="utf-8")
    w_out, full_out = tmp_path / "w.csv", tmp_path / "full.csv"
    extract(xes, w_out)
    full_log.extract(xes, full_out)

    def read(path):
        with path.open(newline="", encoding="utf-8") as stream:
            return list(csv.DictReader(stream))

    w = [(r["activity"], r["resource"]) for r in read(w_out)]
    full = [(r["activity"], r["resource"]) for r in read(full_out) if r["activity"].startswith("W_")]
    assert w == full


# --- against the real log, when it is present --------------------------------------------

@pytest.mark.slow
@pytest.mark.skipif(not (RAW_XES.exists() and SHIPPED_CSV.exists()),
                    reason="needs the raw BPI 2017 XES, which is not tracked in the repo")
def test_regenerating_the_shipped_csv_reproduces_it_byte_for_byte(tmp_path):
    """The stability guarantee, end to end on the real 578 MB log.

    A model id is sha256(MODEL_VERSION : dataset : file_hash of this CSV). If re-extracting
    produced even a different line ending, every cached model would be orphaned and every
    published figure would have to be re-derived. Takes a few minutes; run with `-m slow`.
    """
    out = tmp_path / "BPIC_2017_W.csv"
    meta = extract(RAW_XES, out)
    assert meta["rows"] == 254_382
    assert meta["cases"] == 31_509
    assert meta["instantaneous"] == 0, "no W_ event in this log lacks a lifecycle transition"
    assert out.read_bytes() == SHIPPED_CSV.read_bytes()
    assert meta["csv_sha256"] == file_hash(SHIPPED_CSV)


def test_the_registry_points_at_the_file_this_extractor_writes():
    """The column names written here are the ones the dataset registry reads back."""
    dataset = get_dataset("BPIC_2017_W")
    assert dataset.relative_log_path == "BPIC_2017_W.csv"
    assert [dataset.case_id, dataset.activity_name, dataset.resource,
            dataset.start_timestamp, dataset.end_timestamp] == COLUMNS


SAMPLE_XES = """<?xml version="1.0" encoding="UTF-8"?>
<log xmlns="http://www.xes-standard.org/">
  <trace>
    <string key="concept:name" value="Application_1"/>
    <event>
      <string key="concept:name" value="A_Create Application"/>
      <string key="lifecycle:transition" value="complete"/>
      <date key="time:timestamp" value="2016-01-01T13:00:00.000Z"/>
      <string key="org:resource" value="User_1"/>
    </event>
    <event>
      <string key="concept:name" value="W_Handle leads"/>
      <string key="lifecycle:transition" value="schedule"/>
      <date key="time:timestamp" value="2016-01-01T13:00:04.000Z"/>
      <string key="org:resource" value="User_1"/>
    </event>
    <event>
      <string key="concept:name" value="W_Handle leads"/>
      <string key="lifecycle:transition" value="withdraw"/>
      <date key="time:timestamp" value="2016-01-01T13:01:44.000Z"/>
      <string key="org:resource" value="User_1"/>
    </event>
    <event>
      <string key="concept:name" value="W_Complete application"/>
      <string key="lifecycle:transition" value="start"/>
      <date key="time:timestamp" value="2016-01-02T11:32:57.000Z"/>
      <string key="org:resource" value="User_19"/>
    </event>
    <event>
      <string key="concept:name" value="W_Complete application"/>
      <string key="lifecycle:transition" value="complete"/>
      <date key="time:timestamp" value="2016-01-02T11:43:56.000Z"/>
      <string key="org:resource" value="User_19"/>
    </event>
    <event>
      <string key="concept:name" value="O_Created"/>
      <string key="lifecycle:transition" value="complete"/>
      <date key="time:timestamp" value="2016-01-02T11:43:57.000Z"/>
      <string key="org:resource" value="User_19"/>
    </event>
    <event>
      <string key="concept:name" value="W_Call after offers"/>
      <string key="lifecycle:transition" value="start"/>
      <date key="time:timestamp" value="2016-01-02T11:43:58.000Z"/>
      <string key="org:resource" value="User_19"/>
    </event>
    <event>
      <string key="concept:name" value="W_Call after offers"/>
      <string key="lifecycle:transition" value="suspend"/>
      <date key="time:timestamp" value="2016-01-02T11:46:13.000Z"/>
      <string key="org:resource" value="User_19"/>
    </event>
    <event>
      <string key="concept:name" value="W_Call after offers"/>
      <string key="lifecycle:transition" value="ate_abort"/>
      <date key="time:timestamp" value="2016-02-02T07:00:20.000Z"/>
      <string key="org:resource" value="User_1"/>
    </event>
  </trace>
</log>
"""
