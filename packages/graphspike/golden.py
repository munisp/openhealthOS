"""golden.py — emit the golden outputs as JSON on stdout.
run_age.sh --verify diffs the live AGE results against this."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from synthdata import generate  # noqa: E402
from spike import run_spike    # noqa: E402

if __name__ == "__main__":
    print(json.dumps(run_spike(generate()), indent=2, sort_keys=True))
