# SPDX-License-Identifier: Apache-2.0
"""One period's figures in one object (Phase 5 WP2): flows, KPIs, cost, savings, coverage."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta

from suncore.analysis.baselines import Savings, compute_savings
from suncore.analysis.cost import SlotPrices
from suncore.analysis.energy import MIN_COVERAGE, SlotEnergy
from suncore.analysis.flows import Flows, total_flows
from suncore.analysis.kpi import Kpis, kpis
from suncore.analysis.period import Bucket
from suncore.core.battery import BatteryModel


@dataclass(frozen=True, slots=True)
class PeriodSummary:
    bucket: Bucket
    flows: Flows
    kpis: Kpis
    savings: Savings
    coverage: float
    """Measured slots over the slots the bucket has (23/25 h days included), in [0, 1]."""

    @property
    def complete(self) -> bool:
        return self.coverage >= MIN_COVERAGE


def summarize(
    bucket: Bucket,
    slots: Sequence[SlotEnergy],
    prices: SlotPrices,
    *,
    step: timedelta,
    battery: BatteryModel | None = None,
) -> PeriodSummary:
    """Summarise the ``slots`` that fall into ``bucket``."""
    flows = total_flows(slots)
    expected = bucket.duration / step
    return PeriodSummary(
        bucket=bucket,
        flows=flows,
        kpis=kpis(flows, battery),
        savings=compute_savings(slots, prices),
        coverage=min(1.0, len(slots) / expected) if expected > 0 else 0.0,
    )
