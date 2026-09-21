"""Why is the simulated baseline slower than history? Test the "extreme recorded durations" explanation.

Empirical duration samples include a few enormous working times (a work item left open and
resumed weeks later). When the simulator samples one, that person is blocked for weeks and a
queue builds behind them. This script measures how much of the gap that explains, without
changing the saved model: it caps durations in memory and re-runs the validation.

    python scripts/diagnose_baseline_gap.py [model_id]

Needs a model learned from BPIC_2017_W in runs/workbench/models/ (version 3 or newer).
"""
import copy
import json
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from webapp.workbench.schemas import ExperimentRequest  # noqa: E402
from webapp.workbench.simulation import simulate, validate  # noqa: E402

CAPS_HOURS = [24, 8]
HORIZON, REPETITIONS, SEED = 30, 3, 42


def load_model():
    if len(sys.argv) > 1:
        return json.load(open(ROOT / "runs" / "workbench" / "models" / f"{sys.argv[1]}.json"))
    for path in sorted((ROOT / "runs" / "workbench" / "models").glob("*.json")):
        model = json.load(open(path))
        if model["dataset"] == "BPIC_2017_W" and model.get("reference"):
            return model
    raise SystemExit("Learn BPIC_2017_W in the Process Lab first.")


def report(model, label):
    request = ExperimentRequest(model_id=model["model_id"], name="diagnosis", horizon_days=HORIZON, repetitions=REPETITIONS, seed=SEED)
    runs = [simulate(model, request, SEED + i, baseline=True)[0] for i in range(REPETITIONS)]
    result = validate(model, runs, HORIZON)
    print(f"\n{label}")
    for row in result["cycle"]:
        print(f"  {row['label']:30s} real {row['real']:6.1f}  simulated {row['simulated']:6.1f}  {row['difference_pct']:+5.1f}%")
    top3 = sum(q["share_pct"] for q in result["queue_resources"][:3])
    wait = np.mean([r["metrics"]["mean_wait_hours"] for r in runs])
    print(f"  three most-queued people hold {top3:.0f}% of all queueing; mean queue wait per case {wait:.1f} h")


def main():
    model = load_model()
    samples = np.concatenate([np.array(d["samples"]) for p in model["resources"] for d in p["activities"].values()]) / 3600
    samples.sort()
    top = max(1, len(samples) // 1000)
    print(f"{len(samples):,} recorded task durations; the longest {top} (0.1%) hold "
          f"{100 * samples[-top:].sum() / samples.sum():.0f}% of all working time; longest {samples[-1]:.0f} working hours; "
          f"{(samples > 24).sum()} exceed 24 h, {(samples > 100).sum()} exceed 100 h.")
    report(model, "AS LEARNED (no cap)")
    for cap in CAPS_HOURS:
        capped = copy.deepcopy(model)
        for profile in capped["resources"]:
            for detail in profile["activities"].values():
                detail["samples"] = [min(x, cap * 3600) for x in detail["samples"]]
        report(capped, f"DURATIONS CAPPED AT {cap} WORKING HOURS (in memory only)")


if __name__ == "__main__":
    main()
