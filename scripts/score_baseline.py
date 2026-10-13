"""Score Process Lab's baseline against the real held-out log with the five published measures.

    python scripts/score_baseline.py BPIC_2017_W [repetitions] [seed]

Learns (or reuses the cached) model for the dataset, runs unchanged baselines over the held-out
arrival window, and prints the five distances plus the caveats that qualify them.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from processlab.core.datasets import get_dataset           # noqa: E402
from processlab.core.storage import file_hash              # noqa: E402
from processlab.engine.model import MODEL_VERSION          # noqa: E402
from processlab.scoring.metrics import score_model         # noqa: E402
from processlab.service.jobs import workbench              # noqa: E402

import hashlib                                             # noqa: E402


def resolve_model(dataset_name: str) -> dict:
    dataset = get_dataset(dataset_name)
    if not dataset:
        raise SystemExit(f"Unknown dataset: {dataset_name}")
    source = workbench.raw_data / dataset.relative_log_path
    if not source.exists():
        raise SystemExit(f"Source log not found: {source}")
    model_id = hashlib.sha256(
        f"{MODEL_VERSION}:{dataset_name}:{file_hash(source)}".encode()
    ).hexdigest()[:24]
    try:
        return workbench.get_model(model_id)
    except (FileNotFoundError, ValueError):
        raise SystemExit(
            f"No cached model {model_id} for {dataset_name}. Start the app and click "
            f"'Learn resource profiles' first (or POST /api/workbench/models)."
        )


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    dataset_name = sys.argv[1]
    repetitions = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    seed = int(sys.argv[3]) if len(sys.argv) > 3 else 42

    model = resolve_model(dataset_name)
    print(f"Scoring {dataset_name} (model {model['model_id']}, engine {model['engine']})")
    result = score_model(model, repetitions=repetitions, seed=seed)
    ref = result["reference"]
    print(f"  real: {ref['real_cases']:,} of {ref['held_out_cases_total']:,} held-out cases, "
          f"{ref['real_events']:,} events")
    print(f"  window: {ref['horizon_days']} days from {ref['arrival_window'][0][:10]}")
    print(f"  simulated: {result['runs']} baseline repetitions, seed {seed}\n")

    print(f"{'':6s} {'mean':>9s} {'std':>8s}   informative?  measure")
    for code, row in result["metrics"].items():
        flag = "yes" if row["informative"] else "NO "
        print(f"{code:6s} {row['mean']:9.3f} {row['std']:8.3f}   {flag:12s}  {row['name']}")

    print("\nCaveats:")
    for caveat in result["caveats"]:
        print(f"  - {caveat}")


if __name__ == "__main__":
    main()
