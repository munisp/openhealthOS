"""OpenHealth OS — procurement marketplace (module 6).

Framework-priced catalogue, pooled ordering across PHC clusters, and the
killer feature: supplier scorecards built from stock-out and delivery
telemetry — fulfilment performance is published, without mercy.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

# Framework-priced catalogue (tracer SKUs; NGN per unit).
CATALOGUE = {
    "PARA-TAB": {"name": "Paracetamol 500mg x100", "price_ngn": 1200, "tracer": True},
    "AMX-SUS": {"name": "Amoxicillin susp 250mg/5ml", "price_ngn": 2600, "tracer": True},
    "ORS-001": {"name": "ORS sachet", "price_ngn": 900, "tracer": True},
    "ITN-001": {"name": "Insecticide-treated net", "price_ngn": 2100, "tracer": True},
    "MAL-RDT": {"name": "Malaria rapid test", "price_ngn": 1500, "tracer": True},
    "GLV-LAT": {"name": "Latex gloves box", "price_ngn": 3400, "tracer": False},
}


class CatalogueError(Exception):
    pass


def price_order(items: dict[str, int]) -> int:
    """Total NGN for {sku: qty} at framework prices. Unknown SKUs reject."""
    total = 0
    for sku, qty in items.items():
        if sku not in CATALOGUE:
            raise CatalogueError(f"{sku} not in framework catalogue")
        if qty <= 0:
            raise CatalogueError(f"{sku}: quantity must be positive")
        total += CATALOGUE[sku]["price_ngn"] * qty
    return total


def pool_orders(orders: list[dict]) -> dict[str, int]:
    """Aggregate per-facility orders into one pooled order for the cluster.

    orders: [{"facility": "PHC-007", "items": {"PARA-TAB": 10}}, ...]
    Pooled volume is what unlocks framework pricing for single PHCs.
    """
    pooled: dict[str, int] = defaultdict(int)
    for order in orders:
        for sku, qty in order.get("items", {}).items():
            if sku not in CATALOGUE:
                raise CatalogueError(f"{sku} not in framework catalogue")
            pooled[sku] += int(qty)
    return dict(pooled)


@dataclass
class SupplierScorecard:
    """Fulfilment record for one supplier, from delivery + stockout telemetry."""
    supplier: str
    ordered: int = 0
    delivered_on_time: int = 0
    delivered_late: int = 0
    failed: int = 0
    stockout_days_linked: int = 0  # facility stockout days while awaiting this supplier

    @property
    def fulfilment_rate(self) -> float:
        return self.delivered_on_time / self.ordered if self.ordered else 0.0

    @property
    def grade(self) -> str:
        r = self.fulfilment_rate
        if self.ordered < 5:
            return "INSUFFICIENT-DATA"
        return "A" if r >= 0.95 else "B" if r >= 0.85 else "C" if r >= 0.70 else "FAILING"


def build_scorecards(events: list[dict]) -> dict[str, SupplierScorecard]:
    """Fold delivery/stockout events into per-supplier scorecards.

    Event shapes:
      {"type": "order_placed",    "supplier": "X", ...}
      {"type": "delivery",        "supplier": "X", "on_time": true|false}
      {"type": "delivery_failed", "supplier": "X"}
      {"type": "stockout_day",    "supplier": "X"}   # while awaiting delivery
    """
    cards: dict[str, SupplierScorecard] = {}
    for ev in events:
        sup = ev.get("supplier")
        if not sup:
            continue
        card = cards.setdefault(sup, SupplierScorecard(supplier=sup))
        match ev.get("type"):
            case "order_placed":
                card.ordered += 1
            case "delivery":
                if ev.get("on_time"):
                    card.delivered_on_time += 1
                else:
                    card.delivered_late += 1
            case "delivery_failed":
                card.failed += 1
            case "stockout_day":
                card.stockout_days_linked += 1
    return cards
