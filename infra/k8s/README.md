# Kubernetes skeleton — one state data plane

The federal model (ADR-0002): one shared national core, one data plane per
state, no data flow a state doesn't authorise. This directory is the
starting skeleton for a state plane; environments are GitOps-managed
(one environment per state), images signed and staged.

```
namespace.yaml            state-plane namespace (e.g. oh-state-kaduna)
state-plane.yaml          core workloads: postgres (HA in prod), fhir-server,
                          claims-orchestrator, registry-service, sync-ingest
```

Production notes (spec §5.6): multi-AZ; NG-region or on-prem for states that
require data localisation (NDPA 2023); Dapr sidecars for polyglot pub/sub;
Temporal for durable claims workflows; Kubecost budgets per state plane
($2.50/facility/month guardrail); Prometheus/Grafana observability;
Wazuh watches the fleet.
