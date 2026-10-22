"""Rebuild the W_ work-item CSV the simulator trains on, from the raw XES.

    python scripts/extract_work_items.py raw_data/bpi2017/BPI_Challenge_2017.xes raw_data/BPIC_2017_W.csv

Timestamps are written back verbatim, so re-running this on an unchanged log reproduces the
same file byte for byte — and therefore the same model id, and the same cached model.
"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from processlab.extract.work_items import extract

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(json.dumps(extract(args.input, args.output), indent=2))
