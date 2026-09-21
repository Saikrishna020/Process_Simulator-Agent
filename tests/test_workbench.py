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
from webapp.workbench.simulation import arrival_plan, compare, scenario_resources, simulate

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
