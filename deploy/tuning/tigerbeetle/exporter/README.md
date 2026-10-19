# tigerbeetle metrics → services/stats-bridge

TigerBeetle serves no HTTP by design. The platform-owned bridge is
[services/stats-bridge](../../../services/stats-bridge): a stdlib-only Go
service that tails a stats JSONL file (written by a cron/sidecar running
`tigerbeetle inspect` or our ledger client's own counters) and renders
Prometheus text format on :9100. The collector's prometheus receiver
scrapes it. Ownership is ours, so there is no licence boundary to cross.
