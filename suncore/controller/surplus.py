# SPDX-License-Identifier: Apache-2.0
"""Surplus (BRIEF §4.5, plan WP7): what the controlled devices may use right now.

Definitions (W, core signs: grid + = import, battery + = charging, device + = consumption):

* **battery share**: charging power counts as available only when the goals allow it
  (:func:`battery_share_available`); discharging power is always counted (negative) -
  a battery that covers our devices is a deficit the controller must end;
* **site sample** ``s = -grid + battery_share``: power that currently leaves the house
  through the meter or goes into the battery and could be redirected;
* **available sample** ``a = s + sum(real draw of the devices SunCore drives)``: the BRIEF
  §4.5 "export + current power of controlled consumers + battery share". Smoothing ``a``
  rather than ``s`` keeps the controller's own switching out of the smoothing lag: when a
  device switches on, ``s`` drops by its draw and the draw enters the sum, so ``a`` stays
  where it was and the median never shows a "phantom" surplus;
* **total available** ``T = median(a) - reserve``: what the allocator distributes by
  priority (the reserve absorbs measurement noise and small loads);
* **free surplus** ``F = T - sum(committed draw)``: available on top of everything that runs
  (or was just asked to run) now. This is the safety gate's ``GateInputs.surplus``: the
  grid invariant compares it with an action's *added* demand, so the controller and the
  gate agree by construction. The committed draw (``allocation``) counts a change that is
  not yet visible in the measurement with its expected power;
* the **uncertainty** is the spread of the samples in the smoothing window (robust
  standard deviation, at least the gap between the median and the 10 % quantile): cloud
  flicker widens the band, and the allocator only adds load when ``free - uncertainty``
  covers the need. Devices already on are judged by the median, not the lower bound, so
  flicker alone never switches them off.

The smoothing is a median over ``ControlSettings.smoothing_s``; the window is the only
state and the clock is injected (``now`` is passed in).
"""

from __future__ import annotations

import math
import statistics
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

from suncore.core.balance import FlowSnapshot
from suncore.core.config import GoalsConfig
from suncore.core.control import BatteryBand, PowerEstimate
from suncore.core.signals import Quality

MAD_TO_SIGMA: Final = 1.4826
"""Median absolute deviation to standard deviation for a normal distribution."""
DOWNSIDE_QUANTILE: Final = 0.1
"""The low quantile of the window that bounds the uncertainty from below."""


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

    def shift(self, delta: float) -> None:
        """Add ``delta`` to every stored sample (re-base the window)."""
        if delta:
            self._samples = deque((ts, v + delta) for ts, v in self._samples)

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
        """Uncertainty of the median: the larger of the robust standard deviation
        (MAD x 1.4826) and the downside gap ``median - P10``; 0 for fewer than two samples.

        The downside gap matters for cloud flicker: with two levels (sun / cloud) the MAD
        collapses to 0 as soon as one level holds more than half of the window, while the
        low quantile still shows how far the surplus drops.
        """
        if len(self._samples) < 2:
            return 0.0
        values = sorted(v for _, v in self._samples)
        median = statistics.median(values)
        mad = statistics.median(abs(v - median) for v in values)
        low = values[math.floor(DOWNSIDE_QUANTILE * (len(values) - 1))]
        return max(mad * MAD_TO_SIGMA, median - low)

    def latest(self) -> float | None:
        return self._samples[-1][1] if self._samples else None


def battery_share_available(
    goals: GoalsConfig, soc_pct: float | None, band: BatteryBand | None = None
) -> tuple[bool, float | None]:
    """Whether the battery's charging power may go to consumers; with the target that blocks it.

    In this order:

    * a plan band (Phase 4) decides: first its ``priority_until_soc_pct`` against the *live*
      SoC - battery first (not available) while the SoC is below it or unknown, whatever else
      the band says - then ``charge_from_surplus``. A band can therefore only move the surplus
      between battery and consumers; below its priority the consumers never get more than
      without a plan whose priority is unset (``tests/controller/test_surplus.py``);
      ``goals.battery_target`` stays a floor under a plan too (review D1): below it, or with
      an unknown SoC, the battery charges first whatever the band says;
    * a ``battery_target`` the SoC has not reached: the battery charges first - not
      available (an unknown SoC counts as not reached); the target's time of day is left
      to the planner (Phase 4), so the target counts all day;
    * ``climate_before_export`` ("consumers before battery"): available;
    * a ``battery_target`` that is reached: available;
    * no goal at all: not available - the battery charges first and consumers get what
      would be exported (the conservative default; ``UNVERIFIED`` against the
      maintainer's intent, PR question).

    Returns ``(available, blocking target in %)``.
    """
    target = goals.battery_target
    if band is not None:
        priority = band.priority_until_soc_pct
        if priority is not None and (soc_pct is None or soc_pct < priority):
            return False, priority
        if target is not None and (soc_pct is None or soc_pct < target.soc_pct):
            return False, target.soc_pct
        return (not band.charge_from_surplus, None)
    if target is not None and (soc_pct is None or soc_pct < target.soc_pct):
        return False, target.soc_pct
    if goals.climate_before_export or target is not None:
        return True, None
    return False, None


@dataclass(frozen=True, slots=True)
class SiteSurplus:
    """The smoothed site view of one tick."""

    available_w: float | None
    """Median of the available samples ``a`` over the smoothing window (``None`` = unknown)."""
    latest_available_w: float | None
    """The newest unsmoothed available sample."""
    latest_site_w: float | None
    """The newest unsmoothed site sample ``s`` (without the controlled draw)."""
    uncertainty_w: float
    grid_w: float | None
    battery_w: float | None
    soc_pct: float | None
    charging_available: bool
    target_soc_pct: float | None
    """The battery target that keeps the charging power from the consumers."""
    quality: Quality
    ts: datetime

    @property
    def known(self) -> bool:
        """Usable for allocation (ok or stale inputs)."""
        return self.available_w is not None and self.quality in (Quality.OK, Quality.STALE)

    @property
    def trusted(self) -> bool:
        """Usable for adding load: the gate's grid invariant needs ``OK`` quality."""
        return self.available_w is not None and self.quality is Quality.OK

    def total_w(self, reserve_w: float) -> float | None:
        """Total available to the controlled devices (``None`` = unknown)."""
        return None if self.available_w is None else self.available_w - reserve_w

    def free_w(self, reserve_w: float, committed_w: float) -> float | None:
        """Free surplus on top of everything that runs now (``None`` = unknown)."""
        total = self.total_w(reserve_w)
        return None if total is None else total - committed_w

    def gate_estimate(self, reserve_w: float, committed_w: float) -> PowerEstimate | None:
        """The free surplus as the gate's ``GateInputs.surplus`` (never negative)."""
        free = self.free_w(reserve_w, committed_w)
        if free is None:
            return None
        return PowerEstimate(
            value_w=max(0.0, free), uncertainty_w=self.uncertainty_w, quality=self.quality
        )


class SurplusTracker:
    """Keeps the smoothing window and turns a :class:`FlowSnapshot` into a :class:`SiteSurplus`."""

    def __init__(self, smoothing_s: float) -> None:
        self._window = WindowMedian(smoothing_s)
        self._members: dict[str, float] = {}

    def reset(self) -> None:
        self._window.clear()
        self._members = {}

    def _rebase(self, controlled: Mapping[str, float]) -> float:
        """Keep the window consistent when a device joins or leaves the controlled set.

        A device that leaves (held, unknown estimate) was part of the past samples with its
        last draw; one that joins was not. Shifting the stored samples by the difference
        stops a membership change from looking like a surplus step (the median would
        otherwise show a phantom surplus for half the window).
        """
        delta = 0.0
        for device_id, draw in controlled.items():
            if device_id not in self._members:
                delta += max(0.0, draw)
        for device_id, draw in self._members.items():
            if device_id not in controlled:
                delta -= draw
        self._members = {k: max(0.0, v) for k, v in controlled.items()}
        self._window.shift(delta)
        return sum(self._members.values())

    def update(
        self,
        now: datetime,
        flows: FlowSnapshot | None,
        goals: GoalsConfig,
        band: BatteryBand | None = None,
        *,
        controlled_draw: Mapping[str, float] | None = None,
    ) -> SiteSurplus:
        """Add this tick's sample; ``controlled_draw`` = real draw per controllable device."""
        controlled_w = self._rebase(controlled_draw or {})
        grid = flows.grid_w.value if flows is not None else None
        battery = flows.battery_w.value if flows is not None else None
        soc = flows.battery_soc_pct.value if flows is not None else None
        available, target = battery_share_available(goals, soc, band)
        quality = Quality.UNAVAILABLE
        if flows is not None:
            quality = Quality.worst((flows.grid_w.quality, flows.battery_w.quality))
        if grid is None or battery is None or quality in (Quality.UNAVAILABLE, Quality.INVALID):
            # Unusable input: keep the window (the next good sample continues it) but
            # report nothing - an unknown surplus never adds load.
            return SiteSurplus(
                available_w=None,
                latest_available_w=None,
                latest_site_w=None,
                uncertainty_w=0.0,
                grid_w=grid,
                battery_w=battery,
                soc_pct=soc,
                charging_available=available,
                target_soc_pct=target,
                quality=quality,
                ts=now,
            )
        share = battery if available else min(battery, 0.0)
        site = -grid + share
        sample = site + controlled_w
        self._window.add(now, sample)
        return SiteSurplus(
            available_w=self._window.median(),
            latest_available_w=sample,
            latest_site_w=site,
            uncertainty_w=self._window.spread(),
            grid_w=grid,
            battery_w=battery,
            soc_pct=soc,
            charging_available=available,
            target_soc_pct=target,
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
