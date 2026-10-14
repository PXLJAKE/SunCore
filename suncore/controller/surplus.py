# SPDX-License-Identifier: Apache-2.0
"""Surplus (BRIEF §4.5, plan WP7): what the controlled devices may use right now.

Definitions (W, core signs: grid + = import, battery + = charging, device + = consumption):

* **site surplus** ``s = -grid + battery_share``: power that currently leaves the house
  through the meter or goes into the battery and could be redirected. Negative when the
  house imports or the battery covers a deficit;
* **battery share**: charging power counts as available only when the goals allow it
  (:func:`battery_share_available`); discharging power is always counted (negative) -
  a battery that covers our devices is a deficit the controller must end;
* **total available** ``T = median(s) + sum(draw of devices SunCore allocated) - reserve``.
  The draw of the devices we switched on is part of what we may distribute again (to
  them first, by priority); the reserve absorbs measurement noise and small loads;
* **free surplus** ``F = median(s) - reserve``: available on top of everything that runs
  now. This is what the safety gate's grid invariant compares with an action's *added*
  demand (``GateInputs.surplus``), so the controller and the gate agree by construction;
* the **uncertainty** of the site surplus is the robust spread (median absolute deviation)
  of the samples inside the smoothing window: cloud flicker widens the band, and the
  allocator only adds load when ``free - uncertainty`` covers the need. Devices already on
  are judged by the median, not the lower bound, so flicker alone never switches them off.

The smoothing is a median over ``ControlSettings.smoothing_s``; the window is the only
state and the clock is injected (``now`` is passed in).
"""

from __future__ import annotations

import statistics
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

from suncore.core.balance import FlowSnapshot
from suncore.core.config import GoalsConfig
from suncore.core.control import BatteryBand, PowerEstimate
from suncore.core.signals import Quality

MAD_TO_SIGMA: Final = 1.4826
"""Median absolute deviation to standard deviation for a normal distribution."""


class WindowMedian:
    """Median and robust spread of the samples of the last ``window_s`` seconds."""

    def __init__(self, window_s: float) -> None:
        if window_s < 0:
            msg = "window_s must be >= 0"
            raise ValueError(msg)
        self._window = timedelta(seconds=window_s)
        self._samples: deque[tuple[datetime, float]] = deque()

    def add(self, ts: datetime, value: float) -> None:
        if self._samples and ts < self._samples[-1][0]:
            self.clear()  # the clock went backwards: start over
        self._samples.append((ts, value))
        self._prune(ts)

    def clear(self) -> None:
        self._samples.clear()

    def _prune(self, now: datetime) -> None:
        start = now - self._window
        while self._samples and self._samples[0][0] < start:
            self._samples.popleft()

    def __len__(self) -> int:
        return len(self._samples)

    def median(self) -> float | None:
        if not self._samples:
            return None
        return statistics.median(v for _, v in self._samples)

    def spread(self) -> float:
        """Robust standard deviation (MAD x 1.4826); 0 for fewer than two samples."""
        if len(self._samples) < 2:
            return 0.0
        values = [v for _, v in self._samples]
        median = statistics.median(values)
        mad = statistics.median(abs(v - median) for v in values)
        return mad * MAD_TO_SIGMA

    def latest(self) -> float | None:
        return self._samples[-1][1] if self._samples else None


def battery_share_available(
    goals: GoalsConfig, soc_pct: float | None, band: BatteryBand | None = None
) -> tuple[bool, float | None]:
    """Whether the battery's charging power may go to consumers; with the target that blocks it.

    * a plan band (Phase 4) decides with ``charge_from_surplus`` when given;
    * ``climate_before_export``: consumers before the battery - available;
    * a ``battery_target`` the SoC has not reached: the battery charges first - not
      available (an unknown SoC counts as not reached); the target's time of day is left
      to the planner (Phase 4);
    * otherwise available.
    """
    if band is not None:
        return (not band.charge_from_surplus, None)
    if goals.climate_before_export:
        return True, None
    target = goals.battery_target
    if target is None:
        return True, None
    if soc_pct is None or soc_pct < target.soc_pct:
        return False, target.soc_pct
    return True, None


@dataclass(frozen=True, slots=True)
class SiteSurplus:
    """The smoothed site view of one tick."""

    site_w: float | None
    """Median of ``-grid + battery_share`` over the smoothing window."""
    latest_site_w: float | None
    """The newest unsmoothed sample."""
    uncertainty_w: float
    grid_w: float | None
    battery_w: float | None
    soc_pct: float | None
    charging_available: bool
    quality: Quality
    ts: datetime

    @property
    def known(self) -> bool:
        return self.site_w is not None and self.quality in (Quality.OK, Quality.STALE)

    def free_w(self, reserve_w: float) -> float | None:
        """Free surplus on top of everything that runs now (``None`` = unknown)."""
        return None if self.site_w is None else self.site_w - reserve_w

    def total_w(self, reserve_w: float, allocated_draw_w: float) -> float | None:
        """Total available to the controlled devices (``None`` = unknown)."""
        free = self.free_w(reserve_w)
        return None if free is None else free + allocated_draw_w

    def gate_estimate(self, reserve_w: float) -> PowerEstimate | None:
        """The free surplus as the gate's ``GateInputs.surplus`` (never negative)."""
        free = self.free_w(reserve_w)
        if free is None:
            return None
        return PowerEstimate(
            value_w=max(0.0, free), uncertainty_w=self.uncertainty_w, quality=self.quality
        )


class SurplusTracker:
    """Keeps the smoothing window and turns a :class:`FlowSnapshot` into a :class:`SiteSurplus`."""

    def __init__(self, smoothing_s: float) -> None:
        self._window = WindowMedian(smoothing_s)

    def reset(self) -> None:
        self._window.clear()

    def update(
        self,
        now: datetime,
        flows: FlowSnapshot | None,
        goals: GoalsConfig,
        band: BatteryBand | None = None,
    ) -> SiteSurplus:
        grid = flows.grid_w.value if flows is not None else None
        battery = flows.battery_w.value if flows is not None else None
        soc = flows.battery_soc_pct.value if flows is not None else None
        available, _ = battery_share_available(goals, soc, band)
        quality = Quality.UNAVAILABLE
        if flows is not None:
            quality = Quality.worst((flows.grid_w.quality, flows.battery_w.quality))
        if grid is None or battery is None or quality in (Quality.UNAVAILABLE, Quality.INVALID):
            # Unusable input: keep the window (the next good sample continues it) but
            # report nothing - an unknown surplus never adds load.
            return SiteSurplus(
                site_w=None,
                latest_site_w=None,
                uncertainty_w=0.0,
                grid_w=grid,
                battery_w=battery,
                soc_pct=soc,
                charging_available=available,
                quality=quality,
                ts=now,
            )
        share = battery if available else min(battery, 0.0)
        site = -grid + share
        self._window.add(now, site)
        return SiteSurplus(
            site_w=self._window.median(),
            latest_site_w=site,
            uncertainty_w=self._window.spread(),
            grid_w=grid,
            battery_w=battery,
            soc_pct=soc,
            charging_available=available,
            quality=quality,
            ts=now,
        )


__all__ = [
    "MAD_TO_SIGMA",
    "SiteSurplus",
    "SurplusTracker",
    "WindowMedian",
    "battery_share_available",
]
