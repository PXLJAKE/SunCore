# SPDX-License-Identifier: Apache-2.0
"""The two baselines and the savings against them (Phase 5 WP2, ADR-0014 decision 3).

* *No PV, no battery*: every kWh of the house load comes from the grid.
  ``baseline_no_pv - actual`` is what the plant (PV and battery) adds.
* *PV without battery and without SunCore control*: the house load as measured, no battery
  flows, the PV surplus exported. ``baseline_pv_only - actual`` is what SunCore adds on top of
  the plant; the difference of the two baselines is the PV's own contribution.

Device shifting is not undone: the unshifted device load is unknown. All three costs are
computed over the same priced slots, so ``savings = baseline - actual`` holds exactly.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from suncore.analysis.cost import Cost, SlotPrices, cost_of_grid, priced
from suncore.analysis.energy import SlotEnergy


@dataclass(frozen=True, slots=True)
class Savings:
    actual: Cost
    baseline_no_pv: Cost
    baseline_pv_only: Cost
    skipped_slots: int
    """Slots left out of all three costs because they have no price."""

    @property
    def savings_vs_no_pv(self) -> float:
        """What the plant adds: ``baseline_no_pv - actual``."""
        return self.baseline_no_pv.net - self.actual.net

    @property
    def savings_vs_pv_only(self) -> float:
        """What SunCore adds: ``baseline_pv_only - actual``."""
        return self.baseline_pv_only.net - self.actual.net

    @property
    def pv_contribution(self) -> float:
        """What the PV alone adds: ``baseline_no_pv - baseline_pv_only``."""
        return self.baseline_no_pv.net - self.baseline_pv_only.net


def compute_savings(slots: Iterable[SlotEnergy], prices: SlotPrices) -> Savings:
    rows, skipped = priced(slots, prices)
    return Savings(
        actual=cost_of_grid((s.grid_wh, imp, exp) for s, imp, exp in rows),
        baseline_no_pv=cost_of_grid((max(s.house_wh, 0.0), imp, exp) for s, imp, exp in rows),
        baseline_pv_only=cost_of_grid(
            (max(s.house_wh, 0.0) - max(s.pv_wh, 0.0), imp, exp) for s, imp, exp in rows
        ),
        skipped_slots=skipped,
    )
