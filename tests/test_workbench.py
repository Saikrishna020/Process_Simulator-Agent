from copy import deepcopy
from datetime import datetime, timezone
import json
import time

from fastapi.testclient import TestClient
import pandas as pd
from pydantic import ValidationError
import pytest

from webapp.workbench.calendar import DAY, finish_work, next_work, working_between
from webapp.workbench.model import MODEL_VERSION, _samples, atomic_json, discover, first_available
from webapp.workbench.schemas import ExperimentRequest
from webapp.workbench.service import BusyError, Workbench
from webapp.workbench.simulation import arrival_plan, compare, scenario_resources, simulate, validate

MONDAY = datetime(2026, 1, 5, tzinfo=timezone.utc).timestamp()
CALENDAR = [[9*3600, 17*3600] for _ in range(5)] + [None, None]


@pytest.fixture
def model():
    return dict(model_id="a"*24, version=MODEL_VERSION, dataset="test", source_hash="abc", assumptions=[],
        start_timestamp=MONDAY, historical_cycle_hours=dict(mean=1, median=1, p90=1),
        resources=[dict(id="r0", name="Alice", template_id="r0", calendar=deepcopy(CALENDAR),
                        activities={"Review":dict(samples=[3600])})],
        activities=[dict(name="Review")], templates=[[["Review", "r0", 0]]],
        arrival_days=[[[9*3600]*4]] + [[[]] for _ in range(6)])


def request(**kwargs):
    return ExperimentRequest(model_id="a"*24, horizon_days=7, repetitions=2, **kwargs)


def test_calendar_pauses_at_shift_end_and_skips_weekend():
    start, end = finish_work(MONDAY + 4*DAY + 16*3600, 3*3600, CALENDAR)
    assert start == MONDAY + 4*DAY + 16*3600
    assert end == MONDAY + 7*DAY + 11*3600
    assert working_between(start, end, CALENDAR) == 3*3600
    assert working_between(MONDAY, MONDAY + 14*DAY, CALENDAR) == 80*3600
    assert next_work(MONDAY + 17*3600, CALENDAR) == MONDAY + DAY + 9*3600


def test_empirical_samples_do_not_overweight_the_maximum():
    samples = _samples([1]*999 + [1000])
    assert len(samples) == 1000
    assert samples.count(1000) == 1


def test_delay_uses_first_idle_window_not_the_last_completion():
    intervals = ([MONDAY+9*3600, MONDAY+13*3600], [MONDAY+10*3600, MONDAY+14*3600])
    assert first_available(MONDAY+9*3600, MONDAY+15*3600, CALENDAR, intervals) == MONDAY+10*3600


def test_unchanged_scenario_is_identical_and_model_is_immutable(model):
    original = deepcopy(model)
    baseline, baseline_events = simulate(model, request(), 42, baseline=True)
    scenario, scenario_events = simulate(model, request(), 42)
    assert baseline == scenario
    assert baseline_events == scenario_events
    assert model == original
    metrics = compare([baseline], [scenario])["metrics"]
    assert all(v["delta"]["mean"] == 0 for v in metrics.values())


def test_clone_receives_work_and_reduces_a_known_queue(model):
    req = request(clones=[dict(source_id="r0", name="Bob")])
    baseline, _ = simulate(model, req, 42, baseline=True)
    scenario, events = simulate(model, req, 42)
    assert baseline["metrics"]["mean_wait_hours"] == 1.5
    assert scenario["metrics"]["mean_wait_hours"] == .5
    assert {e["resource"] for e in events} == {"Alice", "Bob"}
    assert scenario["metrics"]["active_resources"] == 2
    assert all(0 <= r["utilization_pct"] <= 100 for r in scenario["resources"])
    for rid in {e["resource_id"] for e in events}:
        rows = sorted((e for e in events if e["resource_id"] == rid), key=lambda e:e["start_timestamp"])
        assert all(a["end_timestamp"] <= b["start_timestamp"] for a,b in zip(rows,rows[1:]))


def test_higher_demand_preserves_shared_cases_and_creates_backlog(model):
    baseline = arrival_plan(model,7,1,42)
    higher = arrival_plan(model,7,2,42)
    assert len(higher) == 2*len(baseline)
    assert higher[:len(baseline)] == baseline
    model["resources"][0]["activities"]["Review"]["samples"] = [24*3600]
    run, events = simulate(model,request(demand_multiplier=2,sla_hours=4),42)
    assert run["metrics"]["backlog_at_horizon"] > 0
    assert run["metrics"]["completed_in_horizon"] + run["metrics"]["backlog_at_horizon"] == len(higher)
    assert run["metrics"]["sla_met_pct"] == 0
    assert all(e["end_timestamp"] >= e["start_timestamp"] for e in events)


def test_resource_removal_rejects_unserviceable_process_and_supports_replacement(model):
    with pytest.raises(ValueError,match="No active resource"):
        scenario_resources(model,request(resource_changes=[dict(resource_id="r0",enabled=False)]))
    run, events = simulate(model,request(resource_changes=[dict(resource_id="r0",enabled=False)],clones=[dict(source_id="r0",name="Replacement")]),42)
    assert run["metrics"]["active_resources"] == 1
    assert all(e["resource"] == "Replacement" for e in events)


def test_schedule_and_activity_changes_apply(model):
    req = request(resource_changes=[dict(resource_id="r0",schedule=dict(weekdays=[0],start_hour=12,end_hour=17))],
                  activity_changes=[dict(activity="Review",duration_multiplier=.5)])
    _, events = simulate(model,req,42)
    assert min(e["start_timestamp"] for e in events) == MONDAY+12*3600
    assert {e["processing_seconds"] for e in events} == {1800}


def test_schema_rejects_invalid_schedules_duplicate_changes_and_nonfinite_values():
    for values in [dict(demand_multiplier=float('nan')), dict(resource_changes=[dict(resource_id="r0"),dict(resource_id="r0")]),
                   dict(clones=[dict(source_id="r0",name="new",schedule=dict(weekdays=[],start_hour=9,end_hour=17))]),
                   dict(resource_changes=[dict(resource_id="r0",schedule=dict(weekdays=[0],start_hour=17,end_hour=9))])]:
        with pytest.raises(ValidationError):
            request(**values)


def event_frame():
    rows=[]
    for day in range(20):
        start=pd.Timestamp('2026-01-01T09:00:00Z')+pd.Timedelta(days=day)
        rows.append(dict(case_id=f'case-{day}',activity='Review',resource='Alice',start=start,end=start+pd.Timedelta(hours=1)))
    return pd.DataFrame(rows)


def test_discovery_uses_only_training_data_and_retains_case_ids():
    frame=event_frame()
    frame.loc[19,'resource']='Only in test'
    model=discover(frame,'test','a'*24,'hash')
    assert model['training_cases']==16
    assert model['test_cases']==4
    assert {r['name'] for r in model['resources']}=={'Alice'}
    assert len(model['templates'])==16
    assert len(model['resources'][0]['activities']['Review']['samples'])==16
    with pytest.raises(ValueError,match='zero duration'):
        discover(frame.assign(end=frame.start),'test','a'*24,'hash')


def test_background_job_persists_separate_artifacts_and_rejects_traversal(tmp_path,model):
    store=Workbench(tmp_path)
    atomic_json(tmp_path/'models'/('a'*24+'.json'),model)
    try:
        store.slot.acquire()
        with pytest.raises(BusyError):
            store.submit('experiment',request())
        store.slot.release()
        job=store.submit('experiment',request(clones=[dict(source_id='r0',name='Bob')]))
        deadline=time.monotonic()+10
        while store.job(job['id'])['status'] in {'queued','running'} and time.monotonic()<deadline:
            time.sleep(.02)
        assert store.job(job['id'])['status']=='done',store.job(job['id'])
        result=store.result(job['id'])
        assert result['comparison']['metrics']['mean_wait_hours']['delta']['mean']==-1
        assert store.download(job['id'],'baseline_1.csv').exists()
        assert store.download(job['id'],'scenario_2.csv').exists()
        with pytest.raises(FileNotFoundError):
            store.download(job['id'],'../../.env')
        with pytest.raises(FileNotFoundError):
            store.get_model('../secret')
    finally:
        store.executor.shutdown()


def test_api_runs_without_llm_and_rejects_invalid_scenarios(tmp_path,model,monkeypatch):
    from webapp import main
    from webapp.workbench import api as module
    store=Workbench(tmp_path)
    atomic_json(tmp_path/'models'/('a'*24+'.json'),model)
    monkeypatch.setattr(module,'workbench',store)
    monkeypatch.setattr(main,'workbench',store)
    def missing_credentials():
        raise RuntimeError('No API key configured')
    monkeypatch.setattr(main,'get_settings',missing_credentials)
    try:
        with TestClient(main.app) as client:
            assert client.get('/').status_code==200
            assert client.get('/chat').status_code==200
            assert client.post('/api/sessions').status_code==503
            assert client.get('/api/workbench/models/'+'a'*24).status_code==200
            assert client.get('/api/workbench/models/unknown').status_code==404
            payload=request(resource_changes=[dict(resource_id='missing')]).model_dump()
            response=client.post('/api/workbench/experiments',json=payload)
            assert response.status_code==422
            assert 'Unknown resource' in response.text
    finally:
        store.executor.shutdown()


def test_research_enabled_times_fall_back_when_windows_denies_process_pipes(monkeypatch):
    from source.extraneous_delays import concurrency_oracle as module
    from source.extraneous_delays.event_log import EventLogIDs
    def denied():
        raise PermissionError('Windows denied multiprocessing pipe creation')
    monkeypatch.setattr(module, 'ProcessPoolExecutor', denied)
    ids=EventLogIDs()
    frame=pd.DataFrame({ids.case:['one','one'],ids.activity:['A','B'],
        ids.start_time:pd.to_datetime(['2026-01-01T09:00:00Z','2026-01-01T11:00:00Z']),
        ids.end_time:pd.to_datetime(['2026-01-01T10:00:00Z','2026-01-01T12:00:00Z'])})
    oracle=module.ConcurrencyOracle({'A':set(),'B':set()}, module.Configuration(log_ids=ids,consider_start_times=True))
    oracle.add_enabled_times(frame,set_nat_to_first_event=True)
    assert pd.isna(frame.loc[0,ids.enabled_time])
    assert frame.loc[1,ids.enabled_time]==frame.loc[0,ids.end_time]


def slow_case_frame():
    """300 daily cases of two tasks ten days apart; the log stops at the end of day 299, so late cases are cut short."""
    rows=[]
    origin=pd.Timestamp('2026-01-01T09:00:00Z')
    for case in range(300):
        for offset,activity in [(0,'Review'),(10,'Approve')]:
            start=origin+pd.Timedelta(days=case+offset)
            rows.append(dict(case_id=f'case-{case}',activity=activity,resource='Alice',start=start,end=start+pd.Timedelta(hours=1)))
    frame=pd.DataFrame(rows)
    return frame[frame.end<=origin+pd.Timedelta(days=299,hours=1)]


def test_reference_leaves_out_held_out_cases_cut_short_by_the_end_of_the_log():
    model=discover(slow_case_frame(),'test','a'*24,'hash')
    ref=model['reference']
    assert ref['censoring_controlled'] and ref['follow_up_days']>=10
    assert ref['cases']<ref['all_test_cases']
    assert ref['cycle_hours']['mean']>ref['all_test_cycle_hours']['mean']
    assert ref['cycle_hours']['median']==pytest.approx(241,abs=1)
    assert model['historical_cycle_hours']==ref['cycle_hours']


def test_reference_falls_back_to_all_held_out_cases_and_says_so_when_history_is_too_short():
    ref=discover(event_frame(),'test','a'*24,'hash')['reference']
    assert ref['cases']==ref['all_test_cases']==4
    assert not ref['censoring_controlled']


def test_explorer_summarises_the_whole_log():
    info=discover(slow_case_frame(),'test','a'*24,'hash')['explore']
    assert info['cases']==300 and info['resource_count']==1 and info['activity_count']==2
    assert info['variants'][0]['steps']==['Review','Approve']
    assert sum(map(sum,info['hour_of_week']))==info['events']
    assert info['handover']['names']==['Alice'] and info['handover']['matrix']==[[1.0]] and info['handover']['same_resource_pct']==100
    approve=next(a for a in info['activities'] if a['name']=='Approve')
    assert approve['median_gap_hours']==pytest.approx(239,abs=1)  # ten days minus the one-hour first task


def full_event_frame():
    """A richer event set for the same 300 cases as slow_case_frame(): each case also gets an
    instantaneous 'A_Submitted' event five minutes before its 'Review' work item, and case 0
    additionally gets a same-instant 'A_Denied' well after its last work item — mimicking how
    BPI 2017's application/offer events extend past the last recorded work item."""
    frame = slow_case_frame()
    origin = pd.Timestamp('2026-01-01T09:00:00Z')
    extra = []
    for case in range(300):
        submitted = origin + pd.Timedelta(days=case) - pd.Timedelta(minutes=5)
        extra.append(dict(case_id=f'case-{case}', activity='A_Submitted', resource='Alice', start=submitted, end=submitted))
    denied = origin + pd.Timedelta(days=309)  # ten days after case 0's last work item
    extra.append(dict(case_id='case-0', activity='A_Denied', resource='Alice', start=denied, end=denied))
    return pd.concat([frame, pd.DataFrame(extra)], ignore_index=True)


def test_full_events_only_change_the_explorer_not_the_simulation_model():
    frame = slow_case_frame()
    baseline = discover(frame, 'test', 'a' * 24, 'hash')
    fuller = discover(frame, 'test', 'a' * 24, 'hash', full_events=full_event_frame())
    # Resource capacity (what the simulator actually uses) must be identical either way.
    assert fuller['resources'] == baseline['resources']
    assert fuller['templates'] == baseline['templates']
    assert fuller['arrival_days'] == baseline['arrival_days']
    # The explorer, however, now sees the extra activity and the later true end for case 0.
    assert baseline['explore']['full_log'] is False
    assert fuller['explore']['full_log'] is True
    assert fuller['explore']['activity_count'] == 4  # Review, Approve, A_Submitted (all cases), A_Denied (case-0 only)
    assert fuller['explore']['events'] > baseline['explore']['events']
    assert fuller['explore']['cycle_days']['p90'] > baseline['explore']['cycle_days']['p90']


def test_full_events_reject_negative_durations():
    frame = slow_case_frame()
    bad = full_event_frame()
    bad.loc[bad.index[-1], 'end'] = bad.loc[bad.index[-1], 'start'] - pd.Timedelta(hours=1)
    with pytest.raises(ValueError, match='negative durations'):
        discover(frame, 'test', 'a' * 24, 'hash', full_events=bad)


def test_validation_compares_the_baseline_with_the_reference_and_skips_old_models(model):
    learned=discover(slow_case_frame(),'test','a'*24,'hash')
    run,_=simulate(learned,request(),42,baseline=True)
    result=validate(learned,[run],7)
    assert [row['label'][:4] for row in result['cycle']]==['Mean','Medi','90th']
    assert {a['name'] for a in result['activities']}=={'Approve'}
    assert result['queue_resources'][0]['name']=='Alice'
    assert validate(model,[run],7) is None
