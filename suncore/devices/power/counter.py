# SPDX-License-Identifier: Apache-2.0
"""Counter-derived power: average power from the deltas of an energy counter.

Devices such as the LG ThinQ air conditioner only publish a cumulative energy counter
("energy today", Wh, updated hourly by the cloud, reset at local midnight). The model
turns counter *changes* into average power over the period the change covers:

* **Quantisation / latency.** With a known update cadence ``U`` (``counter_update_s``,
  LG: 3600) a change observed at ``t`` reports the period ``(t - U, t)``: the counter is
  silent while nothing is consumed, so the elapsed time since the previous change
  over-states the period. The interval length is ``min(elapsed, U)``; without a known
  cadence it is the elapsed time.
* **Quiet counters.** If the counter is alive (sample quality ok) but unchanged for
  ``1.5 U`` the period consumed nothing: the average is ``0``.
* **Midnight reset / going backwards.** A decrease by more than ``BACKWARDS_JITTER_WH``
  re-baselines the counter (the energy of the period around the reset is unknown and
  skipped); smaller decreases are sensor jitter and ignored. Unit ``kWh`` is converted;
  a unit that is not energy yields quality ``invalid`` and no update.
* **Uncertainty** is the quantisation error (one counter step over the period) plus a
  temporal part that rises from a quarter to the full ``span`` as the data ages over one
  update period. The result is monotone in the age; the average of the last period says
  little about the instantaneous power, therefore the temporal part dominates.

Known device state sharpens the estimate: ``off`` means ~0 W; ``on`` without any
history falls back to the nominal power when one is configured.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final

from suncore.core.control import DeviceState, PowerEstimate
from suncore.core.devices import PowerModelKind
from suncore.core.signals import Quality
from suncore.core.units import Dimension
from suncore.devices.power.common import (
    STANDBY_UNCERTAINTY_W,
    Samples,
    age_s,
    make_estimate,
    sample_base_value,
    worst_quality,
)

BACKWARDS_JITTER_WH: Final = 1.0
"""A decrease up to this is sensor jitter, a larger one a reset."""
QUANTISATION_WH: Final = 1.0
"""Counter resolution assumed when judging a delta (integer Wh)."""
IDLE_PERIODS: Final = 1.5
"""A live counter without change for this many update periods consumed nothing."""
MIN_INTERVAL_S: Final = 1.0
MAX_PLAUSIBLE_W: Final = 1_000_000.0
MIN_SPAN_W: Final = 50.0
DEFAULT_UPDATE_S: Final = 60.0
"""Assumed cadence for the age term when none is configured and none was observed."""
TEMPORAL_FLOOR: Final = 0.25
"""Share of ``span`` that remains uncertain right after a counter update."""


@dataclass(frozen=True, slots=True)
class CounterInterval:
    """Energy consumed over ``(start, end)`` as derived from a counter change."""

    start: datetime
    end: datetime
    energy_wh: float

    @property
    def seconds(self) -> float:
        return max(MIN_INTERVAL_S, (self.end - self.start).total_seconds())

    @property
    def avg_w(self) -> float:
        return self.energy_wh * 3600.0 / self.seconds


class CounterDerivedModel:
    """Average power from an energy counter (Wh/kWh)."""

    kind = PowerModelKind.COUNTER_DERIVED

    def __init__(
        self,
        energy_role: str,
        *,
        update_s: float | None = None,
        nominal_w: float | None = None,
    ) -> None:
        if update_s is not None and update_s <= 0:
            msg = "update_s must be positive"
            raise ValueError(msg)
        self._role = energy_role
        self._update_s = update_s
        self._nominal_w = nominal_w
        self._last: tuple[datetime, float] | None = None
        self._interval: CounterInterval | None = None
        self._observed_gaps: list[float] = []
        self._emitted_idle: int = 0
        self._resets = 0
        self._new: list[CounterInterval] = []

    # --- introspection -----------------------------------------------------------

    @property
    def inputs(self) -> tuple[str, ...]:
        return (self._role,)

    @property
    def interval(self) -> CounterInterval | None:
        """The newest derived interval (``None`` before the second distinct counter value)."""
        return self._interval

    @property
    def resets(self) -> int:
        """How many resets (counter decreases beyond jitter) were seen."""
        return self._resets

    @property
    def update_s(self) -> float:
        """The update period in force: configured, else the median observed gap, else default."""
        if self._update_s is not None:
            return self._update_s
        if self._observed_gaps:
            ordered = sorted(self._observed_gaps)
            return max(MIN_INTERVAL_S, ordered[len(ordered) // 2])
        return DEFAULT_UPDATE_S

    def pop_intervals(self) -> list[CounterInterval]:
        """Intervals derived since the last call (consumed by the learned model)."""
        out, self._new = self._new, []
        return out

    # --- observe -----------------------------------------------------------------

    def observe(
        self,
        now: datetime,
        state: DeviceState | None,  # noqa: ARG002
        samples: Samples,
    ) -> None:
        sample = samples.get(self._role)
        value, quality = sample_base_value(sample, Dimension.ENERGY)
        if value is None or sample is None:
            return
        ts = min(sample.ts, now)
        if self._last is None:
            self._last = (ts, value)
            return
        last_ts, last_value = self._last
        if value == last_value:
            if quality is Quality.OK:
                self._observe_idle(now, last_ts)
            return
        if ts < last_ts:
            return  # out-of-order sample
        if value < last_value:
            self._on_decrease(ts, value, last_value)
            return
        self._on_increase(ts, value, last_ts, last_value)

    def _on_decrease(self, ts: datetime, value: float, last_value: float) -> None:
        if last_value - value <= BACKWARDS_JITTER_WH:
            return  # jitter: keep the old baseline
        self._resets += 1  # reset (midnight) or replaced meter: re-baseline, skip the period
        self._last = (ts, value)
        self._emitted_idle = 0

    def _on_increase(
        self, ts: datetime, value: float, last_ts: datetime, last_value: float
    ) -> None:
        delta = value - last_value
        elapsed = (ts - last_ts).total_seconds()
        if self._update_s is None:
            length = max(MIN_INTERVAL_S, elapsed)
        else:
            length = max(MIN_INTERVAL_S, min(elapsed, self._update_s))
        if elapsed >= MIN_INTERVAL_S:
            self._observed_gaps = [*self._observed_gaps[-8:], elapsed]
        avg = delta * 3600.0 / length
        self._last = (ts, value)
        self._emitted_idle = 0
        if avg > MAX_PLAUSIBLE_W:
            return  # glitch: new baseline, no interval
        interval = CounterInterval(ts - timedelta(seconds=length), ts, delta)
        self._interval = interval
        self._new.append(interval)

    def _observe_idle(self, now: datetime, last_ts: datetime) -> None:
        """Emit zero-energy periods for a live but unchanged counter."""
        period = self.update_s
        elapsed = (now - last_ts).total_seconds()
        if elapsed < IDLE_PERIODS * period:
            return
        count = int(elapsed // period)
        while self._emitted_idle < count:
            self._emitted_idle += 1
            end = last_ts + timedelta(seconds=period * self._emitted_idle)
            self._new.append(CounterInterval(end - timedelta(seconds=period), end, 0.0))

    # --- estimate ----------------------------------------------------------------

    def estimate(self, now: datetime, state: DeviceState | None, samples: Samples) -> PowerEstimate:
        sample = samples.get(self._role)
        _, quality = sample_base_value(sample, Dimension.ENERGY)
        interval = self._interval
        if state is not None and state.known and state.on is False:
            # Known off: standby only; the counter cannot tell better.
            age = age_s(now, interval.end) if interval is not None else 0.0
            return make_estimate(
                0.0, STANDBY_UNCERTAINTY_W, worst_quality(quality, state.quality), age
            )
        if interval is None:
            return self._without_history(state, quality)
        age = age_s(now, interval.end)
        period = self.update_s
        idle = (
            quality is Quality.OK
            and self._last is not None
            and (now - self._last[0]).total_seconds() >= IDLE_PERIODS * period
        )
        value = 0.0 if idle else interval.avg_w
        span = max(interval.avg_w, self._nominal_w or 0.0, MIN_SPAN_W)
        quant = QUANTISATION_WH * 3600.0 / interval.seconds
        temporal = span * min(1.0, TEMPORAL_FLOOR + (1.0 - TEMPORAL_FLOOR) * age / period)
        return make_estimate(value, quant + temporal, quality, age)

    def _without_history(self, state: DeviceState | None, quality: Quality) -> PowerEstimate:
        nominal = self._nominal_w
        if nominal is None:
            return make_estimate(0.0, 0.0, worst_quality(quality, Quality.UNAVAILABLE))
        running = state is not None and state.known and state.on is True
        value = nominal if running else 0.5 * nominal
        return make_estimate(value, 0.5 * nominal, worst_quality(quality, Quality.STALE))

    # --- persistence -------------------------------------------------------------

    def dump_state(self) -> dict[str, Any]:
        return {}

    def load_state(self, data: Mapping[str, Any]) -> None:
        del data
