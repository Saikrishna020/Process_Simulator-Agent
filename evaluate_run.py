"""
Standalone evaluation script mirroring analysis_notebooks/evaluation.ipynb,
adapted for the main_results/ output layout produced by simulate.py.

Usage: python evaluate_run.py <log_name e.g. BPIC_2017_W>
"""
import sys
import numpy as np
import pandas as pd

from log_distance_measures.config import EventLogIDs, AbsoluteTimestampType, discretize_to_hour
from log_distance_measures.n_gram_distribution import n_gram_distribution_distance
from log_distance_measures.absolute_event_distribution import absolute_event_distribution_distance
from log_distance_measures.circadian_event_distribution import circadian_event_distribution_distance
from log_distance_measures.relative_event_distribution import relative_event_distribution_distance
from log_distance_measures.cycle_time_distribution import cycle_time_distribution_distance

import warnings
warnings.filterwarnings("ignore")


def align_column_names(df):
    if 'case:concept:name' in df.columns:
        df = df.rename(columns={'case:concept:name': 'case_id'})
    elif 'caseid' in df.columns:
        df = df.rename(columns={'caseid': 'case_id'})
    if 'Activity' in df.columns:
        df = df.rename(columns={'Activity': 'activity'})
    elif 'activity_name' in df.columns:
        df = df.rename(columns={'activity_name': 'activity'})
    elif 'task' in df.columns:
        df = df.rename(columns={'task': 'activity'})
    elif 'concept:name' in df.columns:
        df = df.rename(columns={'concept:name': 'activity'})
    if 'Resource' in df.columns:
        df = df.rename(columns={'Resource': 'resource'})
    elif 'user' in df.columns:
        df = df.rename(columns={'user': 'resource'})
    elif 'agent' in df.columns:
        if 'resource' in df.columns:
            df = df.drop(['resource'], axis=1)
        df = df.rename(columns={'agent': 'resource'})
    elif 'org:resource' in df.columns:
        df = df.rename(columns={'org:resource': 'resource'})
    if 'start_timestamp' in df.columns:
        df = df.rename(columns={'start_timestamp': 'start_time'})
    if 'end_timestamp' in df.columns:
        df = df.rename(columns={'end_timestamp': 'end_time'})
    return df


def evaluate(log_name, num_simulations=10, output_dir=None):
    base = output_dir or f'simulated_data/{log_name}/main_results'
    event_log_ids = EventLogIDs(
        case="case_id", activity="activity",
        start_time="start_time", end_time="end_time", resource='resource'
    )

    test_log = pd.read_csv(f'{base}/test_preprocessed.csv')
    test_log = align_column_names(test_log)
    test_log[event_log_ids.start_time] = pd.to_datetime(test_log[event_log_ids.start_time], utc=True, format='mixed')
    test_log[event_log_ids.end_time] = pd.to_datetime(test_log[event_log_ids.end_time], utc=True, format='mixed')

    all_metrics = {'NGD': [], 'AEDD': [], 'CEDD': [], 'REDD': [], 'CTDD': []}

    for i in range(num_simulations):
        sim_path = f'{base}/simulated_log_{i}.csv'
        simulated_log = pd.read_csv(sim_path)
        simulated_log = align_column_names(simulated_log)
        simulated_log[event_log_ids.start_time] = pd.to_datetime(simulated_log[event_log_ids.start_time], utc=True, format='mixed')
        simulated_log[event_log_ids.end_time] = pd.to_datetime(simulated_log[event_log_ids.end_time], utc=True, format='mixed')

        ngd = n_gram_distribution_distance(test_log, event_log_ids, simulated_log, event_log_ids, n=3)
        all_metrics['NGD'].append(ngd)

        aedd = absolute_event_distribution_distance(
            test_log, event_log_ids, simulated_log, event_log_ids,
            discretize_type=AbsoluteTimestampType.BOTH, discretize_event=discretize_to_hour
        )
        all_metrics['AEDD'].append(aedd)

        cedd = circadian_event_distribution_distance(
            test_log, event_log_ids, simulated_log, event_log_ids,
            discretize_type=AbsoluteTimestampType.BOTH
        )
        all_metrics['CEDD'].append(cedd)

        redd = relative_event_distribution_distance(
            test_log, event_log_ids, simulated_log, event_log_ids,
            discretize_type=AbsoluteTimestampType.BOTH, discretize_event=discretize_to_hour
        )
        all_metrics['REDD'].append(redd)

        ctdd = cycle_time_distribution_distance(
            test_log, event_log_ids, simulated_log, event_log_ids,
            bin_size=pd.Timedelta(hours=1)
        )
        all_metrics['CTDD'].append(ctdd)

        print(f'  run {i}: NGD={ngd:.3f} AEDD={aedd:.2f} CEDD={cedd:.3f} REDD={redd:.2f} CTDD={ctdd:.2f}')

    print(f'\n=== {log_name} — mean over {num_simulations} simulations ===')
    for k, v in all_metrics.items():
        print(f'{k}: {np.mean(v):.3f} (std {np.std(v):.3f})')

    return all_metrics


if __name__ == '__main__':
    log_name = sys.argv[1]
    num_sims = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    evaluate(log_name, num_sims)
