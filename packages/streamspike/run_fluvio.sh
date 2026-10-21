#!/bin/sh
# run_fluvio.sh — live cluster verification for ADR-0066.
# Starts a local Fluvio cluster, creates the contract topics, produces
# the fixture, consumes it back, and diffs against the reference
# engine's expectations. Honest failure: no CLI/cluster runtime => exit 2.
set -eu
cd "$(dirname "$0")"

if ! command -v fluvio >/dev/null 2>&1; then
  echo "== fluvio CLI not present; attempting install =="
  if ! curl -fsS --max-time 15 https://hub.infinyon.cloud/install/install.sh -o /tmp/fluvio-install.sh 2>/dev/null; then
    echo "ERROR: cannot reach the Fluvio installer (no network)." >&2
    echo "The goldens (python3 -m pytest .) remain the pinned contract." >&2
    exit 2
  fi
  sh /tmp/fluvio-install.sh >/dev/null 2>&1 || { echo "ERROR: install failed" >&2; exit 2; }
  export PATH="$HOME/.fvm/bin:$HOME/.fluvio/bin:$PATH"
fi

echo "== starting local cluster =="
if ! fluvio cluster start --local 2>/dev/null; then
  echo "ERROR: local cluster start failed (needs container runtime)." >&2
  exit 2
fi

echo "== creating contract topics =="
python3 - <<'PY'
import json, subprocess
for t in json.load(open("topics.json"))["topics"]:
    subprocess.run(["fluvio", "topic", "create", t["name"],
                    "--partitions", str(t["partitions"])], check=True)
PY

echo "== producing fixture =="
while IFS= read -r line; do
  key=$(printf '%s' "$line" | python3 -c "import json,sys; print(json.load(sys.stdin)['facility_id'])")
  printf '%s' "$line" | fluvio produce lakefeed.clinical --key "$key" --raw
done < fixture_events.jsonl

echo "== consuming and diffing =="
fluvio consume lakefeed.clinical -B -d > /tmp/fluvio-consumed.ndjson
python3 - <<'PY'
import json, sys
consumed = [json.loads(l) for l in open("/tmp/fluvio-consumed.ndjson")]
fixture = [json.loads(l) for l in open("fixture_events.jsonl")]
assert [e["event_id"] for e in consumed] == [e["event_id"] for e in fixture], \
    "cluster output diverges from the reference contract"
print("live fluvio verify OK: %d events, order + identity match" % len(consumed))
PY
