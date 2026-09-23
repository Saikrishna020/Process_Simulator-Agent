"""Extract the full BPI 2017 event log (application, offer AND work-item events) for Data
Explorer's descriptive charts. Does not touch or affect the simulator's W_-only CSV/model.
"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from webapp.workbench.full_log import extract

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(json.dumps(extract(args.input, args.output), indent=2))
