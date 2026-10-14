# SPDX-License-Identifier: Apache-2.0
"""Learned power model: online recursive least squares on the device's operating state.

Features (all scaled to O(1), device operating point only - no I/O):

``[1, on, on*heat, on*cool, on*|setpoint - room|/5, on*cool*(outdoor - 20)/10,
on*heat*(20 - outdoor)/10]``

``heat`` / ``cool`` come from the HVAC mode (``auto`` and ``heat_cool`` are resolved by the
sign of ``setpoint - room``; ``dry`` / ``fan_only`` count as neither). Missing room or
outdoor temperatures zero their features, so the model degrades to on/off and mode.

Training targets are *measured* power samples (one per distinct sample) and *counter
intervals* from the embedded :class:`CounterDerivedModel` (the time-weighted mean of the
features over the interval against the interval's average power, only when the feature
history covers most of it). The prior is the nominal power (``on`` weight = ``nominal_w``)
with a covariance that lets a handful of samples move it; output is clamped to
``[0, 2 * nominal_w]``. Until ``min_samples`` updates have been absorbed the model reports
the nominal estimate instead of the regression (``learned`` equals ``nominal`` at start).

The grid-balance validator (:class:`GridBalanceValidator`) watches switches of the device
and widens the reported uncertainty when the house load does not move as predicted.
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any, Final

from suncore.core.control import DeviceState, PowerEstimate
from suncore.core.devices import PowerModelKind
from suncore.core.signals import Quality, Sample
from suncore.core.units import Dimension
from suncore.devices.power.balance import GridBalanceValidator
from suncore.devices.power.common import (
    DEFAULT_MEASURED_CADENCE_S,
    HOUSE_POWER,
    OUTDOOR_TEMP,
    ROOM_TEMP,
    STANDBY_UNCERTAINTY_W,
    Samples,
    age_s,
    clamp,
    make_estimate,
    sample_base_value,
    state_quality,
    worst_quality,
)
from suncore.devices.power.counter import CounterDerivedModel
from suncore.devices.power.measured import MeasuredPowerModel
from suncore.devices.power.rls import RecursiveLeastSquares

STATE_VERSION: Final = 1
FEATURES: Final = 7
MIN_SAMPLES: Final = 5
MIN_COVERAGE: Final = 0.7
MIN_TRAIN_INTERVAL_S: Final = 20.0
OFF_MODES: Final = frozenset({"off"})
REFERENCE_C: Final = 20.0


def _device_on(state: DeviceState | None) -> bool | None:
    """On/off from a state, without importing the safety layer."""
    if state is None or not state.known:
        return None
    if state.on is not None:
        return state.on
    if state.mode is not None:
        return state.mode not in OFF_MODES
    if state.step is not None:
        return state.step > 0
    if state.value is not None:
        return state.value > 0
    return None


def _temperature(sample: Sample | None) -> float | None:
    if sample is None or sample.value is None or not math.isfinite(sample.value):
        return None
    if sample.quality in (Quality.UNAVAILABLE, Quality.INVALID):
        return None
    return sample.value


def features(state: DeviceState | None, samples: Samples) -> list[float] | None:
    """Feature vector of the operating point; ``None`` when on/off is unknown."""
    on = _device_on(state)
    if on is None or state is None:
        return None
    x = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    if not on:
        return x
    x[1] = 1.0
    room = _temperature(samples.get(ROOM_TEMP))
    outdoor = _temperature(samples.get(OUTDOOR_TEMP))
    setpoint = state.setpoint_c
    heat = cool = 0.0
    mode = state.mode
    if mode == "heat":
        heat = 1.0
    elif mode == "cool":
        cool = 1.0
    elif mode in ("auto", "heat_cool") and setpoint is not None and room is not None:
        heat, cool = (1.0, 0.0) if setpoint > room else (0.0, 1.0)
    x[2], x[3] = heat, cool
    if setpoint is not None and room is not None:
        x[4] = min(abs(setpoint - room), 20.0) / 5.0
    if outdoor is not None:
        out = clamp(outdoor, -30.0, 50.0)
        x[5] = cool * (out - REFERENCE_C) / 10.0
        x[6] = heat * (REFERENCE_C - out) / 10.0
    return x


class LearnedPowerModel:
    """Power learned from measured samples and/or counter intervals; prior = nominal."""

    kind = PowerModelKind.LEARNED

    def __init__(
        self,
        nominal_w: float,
        *,
        power_role: str | None = None,
        energy_role: str | None = None,
        counter_update_s: float | None = None,
        cadence_s: float | None = None,
        min_samples: int = MIN_SAMPLES,
        validator: GridBalanceValidator | None = None,
    ) -> None:
        if nominal_w <= 0:
            msg = "nominal_w must be positive"
            raise ValueError(msg)
        self._nominal_w = nominal_w
        self._power_role = power_role
        self._energy_role = energy_role
        self._min_samples = min_samples
        self._measured = (
            MeasuredPowerModel(
                power_role,
                nominal_w=nominal_w,
                cadence_s=cadence_s if cadence_s else DEFAULT_MEASURED_CADENCE_S,
            )
            if power_role
            else None
        )
        self._counter = (
            CounterDerivedModel(energy_role, update_s=counter_update_s, nominal_w=nominal_w)
            if energy_role
            else None
        )
        n = nominal_w
        self._rls = RecursiveLeastSquares(
            [0.0, n, 0.0, 0.0, 0.0, 0.0, 0.0],
            [
                30.0**2,
                (0.5 * n) ** 2,
                (0.3 * n) ** 2,
                (0.3 * n) ** 2,
                (0.3 * n) ** 2,
                (0.2 * n) ** 2,
                (0.2 * n) ** 2,
            ],
            noise_variance=(0.1 * n + 20.0) ** 2,
        )
        self._validator = validator if validator is not None else GridBalanceValidator()
        self._history: deque[tuple[datetime, list[float]]] = deque()
        self._last_on: bool | None = None
        self._last_on_x: list[float] | None = None
        self._last_trained_ts: datetime | None = None
        self._last_train_now: datetime | None = None

    # --- introspection -----------------------------------------------------------

    @property
    def inputs(self) -> tuple[str, ...]:
        keys = [ROOM_TEMP, OUTDOOR_TEMP, HOUSE_POWER]
        for role in (self._power_role, self._energy_role):
            if role is not None:
                keys.append(role)
        return tuple(keys)

    @property
    def samples_seen(self) -> int:
        """How many training samples the regression has absorbed."""
        return self._rls.updates

    @property
    def ready(self) -> bool:
        return self._rls.updates >= self._min_samples

    @property
    def validator(self) -> GridBalanceValidator:
        return self._validator

    @property
    def weights(self) -> tuple[float, ...]:
        return tuple(self._rls.weights)

    # --- estimate ----------------------------------------------------------------

    def estimate(self, now: datetime, state: DeviceState | None, samples: Samples) -> PowerEstimate:
        x = features(state, samples)
        quality = state_quality(state)
        if x is None:
            return make_estimate(0.0, self._nominal_w, worst_quality(quality, Quality.UNAVAILABLE))
        if not self.ready:
            return self._nominal(x[1] > 0, quality)
        value = clamp(self._rls.predict(x), 0.0, 2.0 * self._nominal_w)
        sigma = math.sqrt(self._rls.prediction_variance(x)) * self._validator.factor
        if x[1] == 0.0:
            sigma = max(sigma, STANDBY_UNCERTAINTY_W)
        age = age_s(now, state.ts) if state is not None else 0.0
        return make_estimate(value, min(sigma, 2.0 * self._nominal_w), quality, age)

    def _nominal(self, on: bool, quality: Quality) -> PowerEstimate:
        if not on:
            return make_estimate(0.0, STANDBY_UNCERTAINTY_W, quality)
        unc = 0.5 * self._nominal_w * self._validator.factor
        return make_estimate(self._nominal_w, min(unc, 2.0 * self._nominal_w), quality)

    # --- observe / learn ---------------------------------------------------------------

    def observe(self, now: datetime, state: DeviceState | None, samples: Samples) -> None:
        if self._measured is not None:
            self._measured.observe(now, state, samples)
        if self._counter is not None:
            self._counter.observe(now, state, samples)
        x = features(state, samples)
        if x is not None:
            self._record(now, x)
        self._train_measured(now, x, samples)
        self._train_counter()
        self._validate(now, state, samples)

    def _record(self, now: datetime, x: list[float]) -> None:
        if (
            self._history
            and self._history[-1][1] == x
            and (now - self._history[-1][0]).total_seconds() < 60
        ):
            return
        if self._history and now <= self._history[-1][0]:
            return
        self._history.append((now, x))
        horizon = max(7200.0, 2.5 * (self._counter.update_s if self._counter else 0.0))
        limit = now - timedelta(seconds=horizon)
        while len(self._history) > 2 and self._history[1][0] < limit:
            self._history.popleft()

    def _train_measured(self, now: datetime, x: list[float] | None, samples: Samples) -> None:
        if self._power_role is None or self._measured is None or x is None:
            return
        sample = samples.get(self._power_role)
        value, quality = sample_base_value(sample, Dimension.POWER)
        if sample is None or value is None or quality is not Quality.OK:
            return
        if age_s(now, sample.ts) > self._measured.cadence_s:
            return  # old reading: it describes another operating point
        if self._last_trained_ts is not None and sample.ts <= self._last_trained_ts:
            return
        if (
            self._last_train_now is not None
            and (now - self._last_train_now).total_seconds() < MIN_TRAIN_INTERVAL_S
        ):
            return
        self._last_trained_ts = sample.ts
        self._last_train_now = now
        self._rls.update(x, clamp(value, 0.0, 2.0 * self._nominal_w))

    def _train_counter(self) -> None:
        if self._counter is None:
            return
        for interval in self._counter.pop_intervals():
            mean = self._mean_features(interval.start, interval.end)
            if mean is None:
                continue
            self._rls.update(mean, clamp(interval.avg_w, 0.0, 2.0 * self._nominal_w))

    def _mean_features(self, start: datetime, end: datetime) -> list[float] | None:
        """Time-weighted mean of the (piecewise constant) features over ``(start, end)``."""
        total = (end - start).total_seconds()
        if total <= 0 or not self._history:
            return None
        acc = [0.0] * FEATURES
        covered = 0.0
        entries = list(self._history)
        for index, (ts, x) in enumerate(entries):
            seg_end = entries[index + 1][0] if index + 1 < len(entries) else end
            lo = max(ts, start)
            hi = min(seg_end, end)
            span = (hi - lo).total_seconds()
            if span <= 0:
                continue
            covered += span
            for i in range(FEATURES):
                acc[i] += x[i] * span
        if covered < MIN_COVERAGE * total:
            return None
        return [v / covered for v in acc]

    def _validate(self, now: datetime, state: DeviceState | None, samples: Samples) -> None:
        house = samples.get(HOUSE_POWER)
        value, quality = sample_base_value(house, Dimension.POWER)
        if value is not None and quality is Quality.OK:
            self._validator.note_house(now, value)
        x = features(state, samples)
        on = _device_on(state)
        if on is not None and self._last_on is not None and on != self._last_on:
            self._note_switch(now, x, now_on=on)
        if on is not None:
            self._last_on = on
            if on and x is not None:
                self._last_on_x = x
        self._validator.evaluate(now)

    def _note_switch(self, now: datetime, x: list[float] | None, *, now_on: bool) -> None:
        """Register the expected house-load change of a switch with the validator."""
        x_on = x if now_on else self._last_on_x
        if x_on is None or x_on[1] == 0.0:
            return
        if self.ready:
            value = clamp(self._rls.predict(x_on), 0.0, 2.0 * self._nominal_w)
        else:
            value = self._nominal_w
        sigma = math.sqrt(self._rls.prediction_variance(x_on))
        self._validator.note_switch(now, value if now_on else -value, sigma)

    # --- persistence -------------------------------------------------------------

    def dump_state(self) -> dict[str, Any]:
        return {
            "version": STATE_VERSION,
            "nominal_w": self._nominal_w,
            "rls": self._rls.dump(),
            "validator": self._validator.dump_state(),
        }

    def load_state(self, data: Mapping[str, Any]) -> None:
        """Restore a dumped state; a different version or nominal power starts afresh."""
        if data.get("version") != STATE_VERSION:
            return
        nominal = data.get("nominal_w")
        if (
            not isinstance(nominal, int | float)
            or abs(nominal - self._nominal_w) > 0.01 * self._nominal_w
        ):
            return
        rls = data.get("rls")
        if isinstance(rls, dict) and self._rls.load(rls):
            validator = data.get("validator")
            if isinstance(validator, dict):
                self._validator.load_state(validator)
