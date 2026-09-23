from datetime import datetime, timedelta, timezone
import json

import pandas as pd
import pytest

from webapp.workbench.full_log import _intervals, extract
from webapp.workbench.model import file_hash


def event(activity, transition, hour, resource=None):
    return (activity, transition, datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(hours=hour), resource)


def test_work_items_are_paired_but_application_and_offer_events_are_instantaneous():
    rows = _intervals([
        event('A_Create Application', 'complete', 0, 'Alice'),
        event('W_Validate application', 'start', 1, 'Alice'),
        event('W_Validate application', 'complete', 3, 'Alice'),
        event('O_Create Offer', 'complete', 4, 'Alice'),
        event('A_Pending', 'complete', 5, 'Alice'),
    ])
    by_name = {name: (start, end) for name, resource, start, end in rows}
    assert by_name['A_Create Application'][0] == by_name['A_Create Application'][1]  # instantaneous
    assert by_name['O_Create Offer'][0] == by_name['O_Create Offer'][1]
    assert by_name['A_Pending'][0] == by_name['A_Pending'][1]
    start, end = by_name['W_Validate application']
    assert (end - start).total_seconds() == 2 * 3600  # real duration from the paired lifecycle


def test_a_work_item_complete_without_a_matching_start_is_skipped_not_fabricated():
    rows = _intervals([event('W_Call after offers', 'complete', 0, 'Alice')])
    assert rows == []


def test_resource_falls_back_to_the_paired_start_events_resource():
    rows = _intervals([
        event('W_Validate application', 'start', 0, 'Alice'),
        event('W_Validate application', 'complete', 1, None),
    ])
    assert rows[0][1] == 'Alice'


def test_streaming_extraction_pairs_work_items_and_persists_provenance(tmp_path):
    source = tmp_path / 'tiny.xes'
    source.write_text('''<log xmlns="http://www.xes-standard.org/"><trace><string key="concept:name" value="one"/>
    <event><string key="concept:name" value="A_Create Application"/><string key="org:resource" value="Alice"/><date key="time:timestamp" value="2026-01-01T00:00:00Z"/></event>
    <event><string key="concept:name" value="W_Validate application"/><string key="lifecycle:transition" value="start"/><string key="org:resource" value="Alice"/><date key="time:timestamp" value="2026-01-01T01:00:00Z"/></event>
    <event><string key="concept:name" value="W_Validate application"/><string key="lifecycle:transition" value="complete"/><string key="org:resource" value="Alice"/><date key="time:timestamp" value="2026-01-01T03:00:00Z"/></event>
    <event><string key="concept:name" value="A_Pending"/><string key="org:resource" value="Alice"/><date key="time:timestamp" value="2026-01-02T00:00:00Z"/></event>
    </trace></log>''')
    destination = tmp_path / 'full_log.csv'
    metadata = extract(source, destination)
    rows = pd.read_csv(destination, parse_dates=['start', 'end'])
    assert metadata['cases'] == 1 and metadata['events'] == 3
    assert metadata['csv_sha256'] == file_hash(destination)
    assert json.loads(destination.with_suffix('.json').read_text())['source_sha256'] == file_hash(source)
    work_item = rows[rows.activity == 'W_Validate application'].iloc[0]
    assert (work_item.end - work_item.start).total_seconds() == 2 * 3600
    pending = rows[rows.activity == 'A_Pending'].iloc[0]
    assert pending.start == pending.end


def test_extraction_requires_a_timezone():
    from lxml import etree

    from webapp.workbench.full_log import _events
    trace = etree.fromstring(
        '<trace xmlns="http://www.xes-standard.org/"><string key="concept:name" value="one"/>'
        '<event><string key="concept:name" value="A_Create Application"/>'
        '<date key="time:timestamp" value="2026-01-01T00:00:00"/></event></trace>'
    )
    with pytest.raises(ValueError):
        _events(trace)
