# SPDX-License-Identifier: Apache-2.0
"""Grid cost of a run of slots (Phase 5 WP2, ADR-0014).

Cost is import cost minus export revenue at the slot's prices. Standing charges, one-off fees
and battery degradation are not part of it (plan question 1). Slots without a price are skipped
and counted, never priced at 0.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime

from suncore.analysis.energy import SiteEnergy, SlotEnergy
from suncore.core.series import Grid, PriceSeries, resample_prices

SlotPrices = Mapping[datetime, tuple[float, float]]
"""``slot start (UTC) -> (import price, export price)`` in currency/kWh."""


@dataclass(frozen=True, slots=True)
class Cost:
    import_wh: float
    export_wh: float
    import_cost: float
    export_revenue: float

    @property
    def net(self) -> float:
        """Cost of the period; negative = the site earned money."""
        return self.import_cost - self.export_revenue


def slot_prices(
    prices: PriceSeries, energy: SiteEnergy, *, export_fallback: float | None
) -> dict[datetime, tuple[float, float]]:
    """Prices at the slots of ``energy``; slots with an unknown price are left out.

    The export price comes from the price source if it has one, else ``export_fallback`` (the
    configured feed-in tariff); without either, no slot is priced.
    """
    grid = Grid(start=energy.start, step=energy.step, count=energy.total_slots)
    held = resample_prices(prices, grid)
    exports = held.export_price.values if held.export_price is not None else None
    out: dict[datetime, tuple[float, float]] = {}
    for i, start in enumerate(grid.starts()):
        imp = held.import_price.values[i]
        exp = exports[i] if exports is not None else export_fallback
        if imp is not None and exp is not None:
            out[start] = (imp, exp)
    return out


def priced(
    slots: Iterable[SlotEnergy], prices: SlotPrices
) -> tuple[list[tuple[SlotEnergy, float, float]], int]:
    """The slots that have a price, with it, and the number skipped."""
    kept: list[tuple[SlotEnergy, float, float]] = []
    skipped = 0
    for slot in slots:
        price = prices.get(slot.start)
        if price is None:
            skipped += 1
        else:
            kept.append((slot, price[0], price[1]))
    return kept, skipped


def cost_of_grid(exchange: Iterable[tuple[float, float, float]]) -> Cost:
    """Cost of ``(net grid Wh, import price, export price)`` rows (+ Wh = import)."""
    rows = list(exchange)
    return Cost(
        import_wh=math.fsum(max(g, 0.0) for g, _, _ in rows),
        export_wh=math.fsum(max(-g, 0.0) for g, _, _ in rows),
        import_cost=math.fsum(max(g, 0.0) / 1000 * imp for g, imp, _ in rows),
        export_revenue=math.fsum(max(-g, 0.0) / 1000 * exp for g, _, exp in rows),
    )


def actual_cost(slots: Iterable[SlotEnergy], prices: SlotPrices) -> tuple[Cost, int]:
    """What the meter saw: cost of the measured grid exchange, and the slots skipped."""
    rows, skipped = priced(slots, prices)
    return cost_of_grid((s.grid_wh, imp, exp) for s, imp, exp in rows), skipped
