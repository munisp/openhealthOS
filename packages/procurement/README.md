# Procurement (Python) — marketplace logic

Framework-priced catalogue, pooled ordering across PHC clusters, and the
killer feature: **supplier scorecards from stock-out telemetry** — fulfilment
performance is published, without mercy.

- `price_order(items)` — framework pricing; unknown SKUs rejected
- `pool_orders(orders)` — cluster aggregation (volume unlocks the pricing)
- `build_scorecards(events)` — order/delivery/stockout events → per-supplier
  fulfilment rate + grade (A/B/C/FAILING; <5 orders = INSUFFICIENT-DATA)

```bash
python3 -m unittest discover -s packages/procurement
```
