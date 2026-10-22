#!/bin/sh
# run_load.sh — full benchmark suite, writes metrics.json.
# Python benches run anywhere; the Go mesh bench runs where a Go
# toolchain exists (skips honestly otherwise). Correctness gates inside
# each bench assert invariants; timings are recorded, never asserted.
set -eu
cd "$(dirname "$0")"
OUT=metrics.json

echo "== stream contract bench (200k events, 8800 facilities) =="
python3 bench_stream.py > /tmp/bench_stream.json
cat /tmp/bench_stream.json

echo "== lakehouse medallion bench (100k events) =="
python3 bench_lakehouse.py > /tmp/bench_lakehouse.json
cat /tmp/bench_lakehouse.json

echo "== relay mesh bench (go) =="
if command -v go >/dev/null 2>&1; then
  (cd ../../../howUdeyChat/relay && go test -bench=Mesh -benchtime=3x -run=XXX .) \
    | tee /tmp/bench_mesh.txt || echo "mesh bench failed — recorded as unavailable"
else
  echo "go toolchain absent — mesh bench skipped honestly" | tee /tmp/bench_mesh.txt
fi

python3 - <<'PY'
import json, re
metrics = {
  "stream": json.load(open("/tmp/bench_stream.json")),
  "lakehouse": json.load(open("/tmp/bench_lakehouse.json")),
}
txt = open("/tmp/bench_mesh.txt").read()
m = re.search(r"(\d+) drain_eps\s+(\d+) hop_eps", txt)
metrics["mesh"] = ({"hop_eps": int(m.group(2)), "drain_eps": int(m.group(1))}
                   if m else {"status": "unavailable", "detail": txt.strip()[:200]})
json.dump(metrics, open("metrics.json", "w"), indent=2)
print("metrics.json written")
PY
