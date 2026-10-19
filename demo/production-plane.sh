#!/bin/sh
# demo/production-plane.sh — ADR-0036 smoke: the production plane is
# complete and consistent. No docker daemon needed: this checks the
# contracts (ports, routes, compose coverage, k8s coverage) that make
# `compose up` / `kubectl apply` give you the whole platform.
set -eu
cd "$(dirname "$0")/.."

fail() { echo "FAIL: $1" >&2; exit 1; }
ok() { echo "ok: $1"; }

echo "== 1. no duplicate default ports across services"
grep -rh 'addr := ":\|addr = ":\|env("LISTEN", ":' services/*/*.go 2>/dev/null \
  | grep -o ':[0-9]\{4\}' | sort | uniq -d | grep . && fail "duplicate LISTEN defaults" || true
ok "all default ports unique"

echo "== 2. every platform service ships in compose/platform.yaml"
for s in api-gateway claims-orchestrator consent-service dispatch-service \
         equipment-service imaging-service ipd-service media-service \
         notification-service pharmacy-service registry-service \
         scheduling-service shr-service telehealth-service telemed-service \
         training-service ussd-gateway wallet-service wellness-service sync-ingest; do
  grep -q "^  $s:" infra/compose/platform.yaml || fail "$s missing from platform.yaml"
done
ok "20/20 platform services in compose"

echo "== 3. compose yaml parses, volumes resolve"
python3 - <<'PY'
import yaml
d = yaml.safe_load(open('infra/compose/platform.yaml'))
vols = set(d.get('volumes', {}))
for name, svc in d['services'].items():
    for v in svc.get('volumes', []):
        src = v.split(':')[0] if isinstance(v, str) else v.get('source')
        if '/' not in src and src not in vols:
            raise SystemExit(f"FAIL: {name} mounts undefined volume {src}")
print(f"ok: {len(d['services'])} services, all named volumes defined")
PY

echo "== 4. k8s planes parse and cover the extended services"
python3 - <<'PY'
import yaml
names = set()
for f in ['infra/k8s/services-plane.yaml', 'infra/k8s/services-plane-extended.yaml', 'infra/k8s/state-plane.yaml']:
    for doc in yaml.safe_load_all(open(f)):
        if doc and doc['kind'] == 'Deployment':
            names.add(doc['metadata']['name'])
want = {'api-gateway','claims-orchestrator','registry-service','consent-service',
        'shr-service','sync-ingest','telehealth-service','wallet-service',
        'equipment-service','pharmacy-service','notification-service','ussd-gateway',
        'scheduling-service','dispatch-service','telemed-service','media-service',
        'imaging-service','training-service','ipd-service','wellness-service'}
missing = want - names
if missing:
    raise SystemExit(f"FAIL: k8s planes missing deployments: {sorted(missing)}")
print(f"ok: k8s covers {len(names)} workloads, none missing")
PY

echo "== 5. gateway route table covers every service (go test)"
(cd services/api-gateway && go test -run 'TestRouteTable|TestRoutePrefixes|TestDefaultUpstream' ./... >/dev/null)
ok "route coverage, uniqueness, port agreement all green"

echo
echo "production-plane smoke: ALL GREEN"
