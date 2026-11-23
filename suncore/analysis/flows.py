# SPDX-License-Identifier: Apache-2.0
"""Energy flows by the fixed attribution rule (Phase 5 WP2, ADR-0014 decision 2).

Per slot, from PV, house load and the battery's charge/discharge:

1. PV serves the house first;
2. the remaining PV charges the battery;
3. what is left is exported;
4. the grid charges the battery only for what PV could not;
5. the battery discharge serves the house first (the rest of it, if any, is exported);
6. the grid serves what is still missing at the house.

Every flow is non-negative and the four balances hold exactly:
``pv = pv_to_house + pv_to_battery + pv_to_grid``,
``house = pv_to_house + battery_to_house + grid_to_house``,
``charge = pv_to_battery + grid_to_battery``,
``discharge = battery_to_house + battery_to_grid``.
``residual_wh`` is the measured net grid minus the modelled net grid (measurement error, losses
the house value did not include); it is 0 for a consistent measurement.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass

from suncore.analysis.energy import SlotEnergy


@dataclass(frozen=True, slots=True)
class Flows:
    """Attributed energy in Wh; all fields >= 0 except ``residual_wh``."""

    pv_to_house_wh: float = 0.0
    pv_to_battery_wh: float = 0.0
    pv_to_grid_wh: float = 0.0
    battery_to_house_wh: float = 0.0
    battery_to_grid_wh: float = 0.0
    grid_to_house_wh: float = 0.0
    grid_to_battery_wh: float = 0.0
    residual_wh: float = 0.0

    @property
    def pv_wh(self) -> float:
        return self.pv_to_house_wh + self.pv_to_battery_wh + self.pv_to_grid_wh

    @property
    def house_wh(self) -> float:
        return self.pv_to_house_wh + self.battery_to_house_wh + self.grid_to_house_wh

    @property
    def charge_wh(self) -> float:
        return self.pv_to_battery_wh + self.grid_to_battery_wh

    @property
    def discharge_wh(self) -> float:
        return self.battery_to_house_wh + self.battery_to_grid_wh

    @property
    def import_wh(self) -> float:
        return self.grid_to_house_wh + self.grid_to_battery_wh

    @property
    def export_wh(self) -> float:
        return self.pv_to_grid_wh + self.battery_to_grid_wh


def slot_flows(slot: SlotEnergy) -> Flows:
    """Attribute one slot. Negative PV or house readings (sensor noise) count as 0."""
    pv = max(slot.pv_wh, 0.0)
    house = max(slot.house_wh, 0.0)
    charge = max(slot.battery_wh, 0.0)
    discharge = max(-slot.battery_wh, 0.0)

    pv_to_house = min(pv, house)
    pv_to_battery = min(pv - pv_to_house, charge)
    pv_to_grid = pv - pv_to_house - pv_to_battery
    grid_to_battery = charge - pv_to_battery
    battery_to_house = min(discharge, house - pv_to_house)
    battery_to_grid = discharge - battery_to_house
    grid_to_house = house - pv_to_house - battery_to_house

    modelled_net = grid_to_house + grid_to_battery - pv_to_grid - battery_to_grid
    return Flows(
        pv_to_house_wh=pv_to_house,
        pv_to_battery_wh=pv_to_battery,
        pv_to_grid_wh=pv_to_grid,
        battery_to_house_wh=battery_to_house,
        battery_to_grid_wh=battery_to_grid,
        grid_to_house_wh=grid_to_house,
        grid_to_battery_wh=grid_to_battery,
        residual_wh=slot.grid_wh - modelled_net,
    )


def total_flows(slots: Iterable[SlotEnergy]) -> Flows:
    """Sum of the per-slot flows (attribution is per slot, never on period totals)."""
    items = [slot_flows(s) for s in slots]
    return Flows(
        pv_to_house_wh=math.fsum(f.pv_to_house_wh for f in items),
        pv_to_battery_wh=math.fsum(f.pv_to_battery_wh for f in items),
        pv_to_grid_wh=math.fsum(f.pv_to_grid_wh for f in items),
        battery_to_house_wh=math.fsum(f.battery_to_house_wh for f in items),
        battery_to_grid_wh=math.fsum(f.battery_to_grid_wh for f in items),
        grid_to_house_wh=math.fsum(f.grid_to_house_wh for f in items),
        grid_to_battery_wh=math.fsum(f.grid_to_battery_wh for f in items),
        residual_wh=math.fsum(f.residual_wh for f in items),
    )
