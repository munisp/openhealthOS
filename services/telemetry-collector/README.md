# Telemetry collector (Rust) — the infrastructure layer

Module 9, the differentiator: IoT-lite facility telemetry as first-class data.
Reads sensor readings as JSON lines on stdin, evaluates alert rules, emits
alert JSON lines on stdout. Dependency-free (std only) — builds on the edge.

## Metrics and rules

| Metric | Rule | Severity |
|---|---|---|
| `cold_chain_c` | outside the WHO 2–8°C vaccine band | CRITICAL |
| `battery_soc_pct` | below 20% — facility going dark | CRITICAL |
| `water_tank_pct` | below 10% — facility going dry | WARNING |
| `grid_up` | grid down, running on solar/battery | INFO |

Alerts queue via SMS when data is absent (ADR-0001) and roll up to the
facility-readiness dashboards — the auditable record the platform shows
funders: which PHC is dark, dry, or hot, before a vaccine dies.

## Run / test

```bash
echo '{"facility":"PHC-007","metric":"cold_chain_c","value":9.2}' | cargo run
cargo test
```
