# ADR-0043: Cilium/eBPF network plane on the k8s state planes

Status: accepted (2026-10-20)

## Context

The k8s skeleton (infra/k8s) hardens pods (restricted PSS, read-only
root FS) but the network was unenforced: any pod could reach any pod.
A plane holding PHI and moving claims money deserves a network that
enforces the architecture rather than trusting it. Stock Kubernetes
NetworkPolicy is L3/L4 only and (with many CNIs) enforced through
per-service iptables chains — real latency on a plane sized for
~200k events/s ingest.

## Decision

Cilium (Apache-2.0 — licence-clean per doctrine) as the CNI on state
and services planes, with:

- **kube-proxy replacement** — eBPF maps instead of iptables; O(1)
  service lookup at ingest scale.
- **Default-deny + explicit allows** (`network-policies.yaml`) — every
  allow rule mirrors a dependency already declared in env vars
  (`DATABASE_URL`, `SWITCH`…). A workload that gains creds without a
  policy change is refused by the network: drift is loud, not silent.
- **L7 HTTP rules where they pay** — sync-ingest accepts only
  `POST /ingest`, `POST /sync/merge`, `GET /healthz|/metrics`; anything
  else is dropped in-kernel before it reaches the process.
- **WireGuard transparent encryption** pod-to-pod — network-layer belt
  under the app-layer mTLS of ADR-0025, catching anything that bypasses
  the proxy.
- **Hubble → ADR-0040 observability plane** — flow logs and
  dns/drop/tcp/http metrics scraped by the plane's existing Prometheus
  (ServiceMonitors on), so network truth sits next to application spans
  and per-tenant metrics in the same Grafana.
- **eBPF host firewall** — node-level policy, not just pod-level.

## Boundary, stated honestly

- Facility **edge nodes run bare docker-compose** on 4-core boxes:
  Cilium adds nothing there and is not deployed there. Edge hardening
  stays ADR-0042 (Caddy) + the compose restart/journal doctrine.
- Cilium does not replace Wazuh (host/file integrity) or open-appsec
  (HTTP payload inspection): eBPF sees flows, not request bodies.
  The layers are complementary, and each one's README says so.

## Consequences

- First deploy requires the CNI swap at cluster build time (helm values
  in `infra/k8s/cilium/values.yaml`); retrofitting a live plane means a
  node-pool rotation — schedule it with the state's maintenance window.
- Kernel >= 5.10 on state-plane nodes (already the baseline for Ubuntu
  22.04 LTS images in deploy runbooks).
- Hubble UI is internal-only (monitoring namespace, default-deny in
  force) — it is a debugging tool, not a tenant surface.
