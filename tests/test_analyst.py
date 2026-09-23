from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
import time

import pandas as pd
import pytest
from pydantic import ValidationError

from webapp.workbench.analyst import AnalysisPlan, AnalysisRequest, Analyst, DataCatalog, Intent, capabilities, execute, plan_question
from webapp.workbench.model import atomic_json, file_hash
from webapp.workbench.outcomes import case_outcome, extract


def event(activity, hour, offer=None):
    return dict(activity=activity, timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc)+timedelta(hours=hour), OfferID=offer)


def test_response_intervals_are_matched_and_union_not_double_counted():
    events = [event('A_Create Application',0), event('O_Create Offer',1,'one'), event('O_Created',1,'one'),
              event('O_Sent (online only)',2,'one'), event('O_Sent (online only)',3,'two'),
              event('O_Returned',6,'one'), event('O_Returned',8,'two'), event('O_Returned',9,'unmatched'),
              event('A_Incomplete',10), event('W_Validate application',11), event('W_Validate application',12),
              event('A_Pending',13), event('A_Cancelled',14)]
    row = case_outcome('case', list(reversed(events)))
    assert row['customer_response_hours'] == 6
    assert row['response_pairs'] == 2
    assert row['offer_count'] == 1
    assert row['rework_count'] == 1
    assert row['outcome'] == 'Cancelled'
    assert row['cycle_days'] == pytest.approx(14/24)


def test_unreturned_offers_are_missing_not_zero():
    row = case_outcome('case', [event('O_Sent (online only)',0,'one'), event('A_Denied',20)])
    assert row['customer_response_hours'] is None
    assert row['unreturned_offer_count'] == 1


def test_streaming_extraction_persists_provenance(tmp_path):
    source = tmp_path/'tiny.xes'
    source.write_text('''<log xmlns="http://www.xes-standard.org/"><trace><string key="concept:name" value="one"/>
    <event><string key="concept:name" value="A_Create Application"/><date key="time:timestamp" value="2026-01-01T00:00:00Z"/></event>
    <event><string key="concept:name" value="A_Pending"/><date key="time:timestamp" value="2026-01-02T00:00:00Z"/></event>
    </trace></log>''')
    destination = tmp_path/'outcomes.csv'
    metadata = extract(source,destination)
    row = pd.read_csv(destination).iloc[0]
    assert row.outcome == 'Pending' and row.cycle_days == 1
    assert metadata['cases'] == 1 and metadata['csv_sha256'] == file_hash(destination)
    assert json.loads(destination.with_suffix('.json').read_text())['source_sha256'] == file_hash(source)


@pytest.fixture
def store(tmp_path):
    raw = tmp_path/'raw'; raw.mkdir()
    events = pd.DataFrame(dict(case_id=['a','a','b','b','c','c'],activity=['Review','Approve']*3,
        resource=['Alice','Bob']*3, start_time=['2026-01-01T09:00:00Z','2026-01-01T12:00:00Z']*3,
        end_time=['2026-01-01T10:00:00Z','2026-01-01T13:00:00Z']*3))
    source = raw/'LoanApp.csv.gz'; events.to_csv(source,index=False)
    outcomes = pd.DataFrame(dict(case_id=['a','b','c'],outcome=['Pending','Pending','Denied'],cycle_days=[1,3,10],
        rework_count=[0,1,0],offer_count=[1,1,2],customer_response_hours=[2,None,6],
        first_start=['2026-01-01T00:00:00Z']*3,last_end=['2026-01-02T00:00:00Z']*3))
    companion=raw/'LoanApp.outcomes.csv'; outcomes.to_csv(companion,index=False)
    atomic_json(companion.with_suffix('.json'),dict(csv_sha256=file_hash(companion),source_sha256='xes-source'))
    model = dict(dataset='LoanApp',source_hash=file_hash(source),model_id='a'*24)
    return SimpleNamespace(root=tmp_path/'runs',raw_data=raw,get_model=lambda _:model)


def test_aggregates_missingness_filters_and_conditional_shares(store):
    data=DataCatalog().load(store,'a'*24)
    result=execute(data,AnalysisPlan(metric='customer_response_hours',aggregation='mean',group_by='outcome'))
    pending=next(r for r in result['rows'] if r['label']=='Pending')
    assert pending['value']==2 and pending['records']==2 and pending['missing']==1
    assert result['missing_records']==1
    filtered=execute(data,AnalysisPlan(metric='cycle_days',aggregation='median',group_by=None,
        filters=[dict(field='outcome',value='Pending')]))
    assert filtered['rows'][0]['value']==2 and filtered['matching_records']==2
    selected=execute(data,AnalysisPlan(filters=[dict(field='outcome',operator='in',value=['Pending','Denied'])]))
    assert selected['matching_records']==3
    split=execute(data,AnalysisPlan(group_by='offer_band',split_by='outcome'))
    assert all(r['within_group_pct']==100 for r in split['rows'])
    assert sum(r['share_pct'] for r in split['rows'])==pytest.approx(100)
    assert capabilities(data)['customer_wait']['observed']==2


def test_event_gaps_exclude_first_events_and_empty_populations_are_safe(store):
    data=DataCatalog().load(store,'a'*24)
    result=execute(data,AnalysisPlan(table='events',metric='gap_hours',aggregation='mean',group_by='activity'))
    approve=next(r for r in result['rows'] if r['label']=='Approve')
    assert approve['value']==2
    assert result['missing_records']==3
    empty=execute(data,AnalysisPlan(filters=[dict(field='outcome',value='does not exist')]))
    assert empty['matching_records']==0 and empty['rows']==[]
    json.dumps(empty,allow_nan=False)


def test_query_boundaries_reject_unavailable_fields_code_and_bad_filters(store):
    data=DataCatalog().load(store,'a'*24)
    with pytest.raises(ValidationError):
        AnalysisPlan(metric='__import__("os")')
    with pytest.raises(ValidationError):
        AnalysisPlan(sql='DROP TABLE events')
    with pytest.raises(ValidationError):
        AnalysisPlan(metric='cycle_days',aggregation='count')
    with pytest.raises(ValueError,match='Unavailable'):
        execute(data,AnalysisPlan(table='cases',group_by='resource'))
    with pytest.raises(ValueError,match='finite'):
        execute(data,AnalysisPlan(filters=[dict(field='cycle_days',value='nan')]))


def test_catalog_reuses_frames_and_invalidates_changed_sources(store):
    catalog=DataCatalog(); data=catalog.load(store,'a'*24)
    assert catalog.load(store,'a'*24) is data
    path=store.raw_data/'LoanApp.outcomes.csv'
    path.write_text(path.read_text().replace('Pending','Unknown'))
    with pytest.raises(ValueError,match='provenance'):
        catalog.load(store,'a'*24)


def test_suggested_question_needs_no_llm(store,monkeypatch):
    from webapp.orchestrator import llm
    monkeypatch.setattr(llm,'build_llm',lambda **_: pytest.fail('Preset must not call a provider'))
    intent=plan_question('Outcome distribution',capabilities(DataCatalog().load(store,'a'*24)))
    assert intent.plan.group_by=='outcome'


def test_language_planner_sees_schema_and_query_but_not_records_or_results(store,monkeypatch):
    from webapp.orchestrator import llm
    captured=[]
    class Planner:
        def with_structured_output(self,schema,method):
            assert schema is Intent and method=='function_calling'
            return self
        def invoke(self,messages,config):
            captured.extend(messages)
            return Intent(plan=AnalysisPlan(metric='cycle_days',aggregation='median',group_by=None,
                filters=[dict(field='outcome',value='Denied')]))
    monkeypatch.setattr(llm,'build_llm',lambda **_:Planner())
    data=DataCatalog().load(store,'a'*24)
    intent=plan_question('Only denied cases',capabilities(data),AnalysisPlan())
    result=execute(data,intent.plan)
    assert result['rows'][0]['value']==10
    assert data['source_hash'] not in captured[0].content
    assert 'customer_wait' not in captured[0].content and 'Alice' not in captured[0].content
    assert 'Previous plan' in captured[0].content


def test_analyses_are_saved_recoverable_and_do_not_fabricate_predictions(store,monkeypatch):
    from webapp.workbench import analyst as module
    service=Analyst()
    monkeypatch.setattr(module,'plan_question',lambda *_:Intent(clarification='Prediction requires a separately validated outcome model.'))
    try:
        job=service.submit(store,AnalysisRequest(model_id='a'*24,question='Predict my best solution'))
        deadline=time.monotonic()+5
        while service.get(store,job['id'])['status']=='running' and time.monotonic()<deadline:
            time.sleep(.01)
        saved=service.get(store,job['id'])
        assert saved['status']=='clarification' and saved['result'] is None
        assert len(service.catalog(store,'a'*24)['history'])==1
        saved['status']='running'; atomic_json(service.folder(store)/f"{job['id']}.json",saved)
        service.recover(store)
        assert service.get(store,job['id'])['status']=='error'
        with pytest.raises(FileNotFoundError): service.get(store,'../../.env')
    finally:
        service.executor.shutdown()
