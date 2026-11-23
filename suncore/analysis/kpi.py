# SPDX-License-Identifier: Apache-2.0
"""Autarky, self-consumption and battery cycles (Phase 5 WP2, ADR-0014)."""

from __future__ import annotations

from dataclasses import dataclass

from suncore.analysis.flows import Flows
from suncore.core.battery import BatteryModel


@dataclass(frozen=True, slots=True)
class Kpis:
    """Ratios in [0, 1]; ``None`` where the denominator is 0 (no load / no PV / no battery)."""

    autarky: float | None
    """Share of the house load not served by the grid: ``1 - grid_to_house / house``."""
    self_consumption: float | None
    """Share of the PV energy used on site (house or battery): ``1 - pv_to_grid / pv``."""
    equivalent_full_cycles: float | None
    """Battery throughput ``(charged + discharged) / 2`` over the usable capacity."""


def _unit(value: float) -> float:
    return min(1.0, max(0.0, value))


def autarky(flows: Flows) -> float | None:
    house = flows.house_wh
    return None if house <= 0 else _unit(1.0 - flows.grid_to_house_wh / house)


def self_consumption(flows: Flows) -> float | None:
    pv = flows.pv_wh
    return None if pv <= 0 else _unit(1.0 - flows.pv_to_grid_wh / pv)


def equivalent_full_cycles(flows: Flows, battery: BatteryModel | None) -> float | None:
    """Full cycles of the usable capacity (floor to ``max_soc_pct``); ``None`` without battery."""
    if battery is None:
        return None
    return (flows.charge_wh + flows.discharge_wh) / 2 / battery.usable_wh


def kpis(flows: Flows, battery: BatteryModel | None = None) -> Kpis:
    return Kpis(
        autarky=autarky(flows),
        self_consumption=self_consumption(flows),
        equivalent_full_cycles=equivalent_full_cycles(flows, battery),
    )
