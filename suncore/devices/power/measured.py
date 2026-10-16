# SPDX-License-Identifier: Apache-2.0
"""Measured power: the newest power sample, with an uncertainty that grows with its age.

The expected cadence matters: a 5 minute polled sensor (``smartthinq_sensors``
``energy_current``) is not stale after 20 seconds. Uncertainty is

``base(value) + growth(age)`` where ``base = noise_w + rel * value`` and ``growth`` rises
linearly from 0 at ``cadence`` to ``span`` at ``3 * cadence`` and stays there; it is
monotone in the age. A sample older than ``cadence * STALE_CADENCE_FACTOR`` is ``stale``.
When the sample is unusable the last good value is reported as ``stale`` /
``unavailable`` with the uncertainty of an old sample.

The age that counts is the age of the *information*, not of the last report: many
sensors only report a changed value (an idle unit sits at 5 W for a day). While the
device's observed state (on/off, step, set-point, mode) has not changed since the
sample, the sample is current (age 0). After a change the age runs from the change, so
a slow sensor that has not caught up yet widens the uncertainty as before. Without a
known device state the raw sample age applies.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, Final

from suncore.core.control import DeviceState, PowerEstimate
from suncore.core.devices import PowerModelKind
from suncore.core.signals import Quality
from suncore.core.units import Dimension
from suncore.devices.power.common import (
    DEFAULT_MEASURED_CADENCE_S,
    STALE_CADENCE_FACTOR,
    Samples,
    age_s,
    make_estimate,
    sample_base_value,
    worst_quality,
)

NOISE_W: Final = 10.0
"""Absolute measurement noise of a power sensor."""
REL_ERROR: Final = 0.02
"""Relative error of a power sensor."""
MIN_SPAN_W: Final = 50.0
"""Smallest uncertainty added by an old sample (a load can change by at least this much)."""


class MeasuredPowerModel:
    """Power from a sensor of the device (or a smart plug)."""

    kind = PowerModelKind.MEASURED

    def __init__(
        self,
        power_role: str,
        *,
        cadence_s: float = DEFAULT_MEASURED_CADENCE_S,
        nominal_w: float | None = None,
    ) -> None:
        if cadence_s <= 0:
            msg = "cadence_s must be positive"
            raise ValueError(msg)
        self._role = power_role
        self._cadence_s = cadence_s
        self._nominal_w = nominal_w
        self._last_good: tuple[float, datetime] | None = None
        self._state_key: tuple[object, ...] | None = None
        self._changed_at: datetime | None = None
        self._state_known = False

    @property
    def inputs(self) -> tuple[str, ...]:
        return (self._role,)

    @property
    def cadence_s(self) -> float:
        return self._cadence_s

    def observe(self, now: datetime, state: DeviceState | None, samples: Samples) -> None:
        self._track_state(now, state)
        value, quality = sample_base_value(samples.get(self._role), Dimension.POWER)
        sample = samples.get(self._role)
        if value is None or sample is None or quality in (Quality.UNAVAILABLE, Quality.INVALID):
            return
        if self._last_good is None or sample.ts >= self._last_good[1]:
            self._last_good = (max(0.0, value), sample.ts)

    def estimate(self, now: datetime, state: DeviceState | None, samples: Samples) -> PowerEstimate:
        sample = samples.get(self._role)
        value, quality = sample_base_value(sample, Dimension.POWER)
        ts = sample.ts if sample is not None else None
        negative_w = 0.0
        if value is None or ts is None:
            # Unusable sample: fall back to the last good value, flagged by the quality.
            if self._last_good is None:
                return self._unknown(quality)
            value, ts = self._last_good
            quality = worst_quality(quality, Quality.STALE)
        elif value < 0:
            negative_w = -value  # noise below zero: clamp, widen by the amount
            value = 0.0
        age = self._information_age(now, ts, state)
        if age > self._cadence_s * STALE_CADENCE_FACTOR:
            quality = worst_quality(quality, Quality.STALE)
        # A sample from before the latest change reports its true age, so the controller
        # sees that the estimate does not reflect the change yet (``unsettled``).
        behind = self._changed_at is not None and ts < self._changed_at
        reported = age_s(now, ts) if behind else age
        return make_estimate(value, self._uncertainty(value, age) + negative_w, quality, reported)

    def _track_state(self, now: datetime, state: DeviceState | None) -> None:
        if state is None or not state.known:
            self._state_known = False
            return
        key = (state.on, state.step, state.setpoint_c, state.mode)
        # Compared with the last *known* state, so a change across an unknown gap counts.
        if self._state_key is not None and key != self._state_key:
            self._changed_at = now
        self._state_key = key
        self._state_known = True

    def _information_age(self, now: datetime, ts: datetime, state: DeviceState | None) -> float:
        if state is None or not state.known or not self._state_known:
            return age_s(now, ts)
        if self._changed_at is not None and ts < self._changed_at:
            return age_s(now, self._changed_at)
        return 0.0

    def _uncertainty(self, value: float, age: float) -> float:
        base = NOISE_W + REL_ERROR * value
        span = max(value, self._nominal_w or 0.0, MIN_SPAN_W)
        over = max(0.0, age - self._cadence_s)
        growth = span * min(1.0, over / (2.0 * self._cadence_s))
        return base + growth

    def _unknown(self, quality: Quality) -> PowerEstimate:
        fallback = self._nominal_w or 0.0
        return make_estimate(0.0, fallback, worst_quality(quality, Quality.UNAVAILABLE))

    def dump_state(self) -> dict[str, Any]:
        return {}

    def load_state(self, data: Mapping[str, Any]) -> None:
        del data
