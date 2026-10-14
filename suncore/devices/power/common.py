# SPDX-License-Identifier: Apache-2.0
"""Shared types and helpers of the power models (Phase 3 WP5, BRIEF §4.5).

A power model turns what is known about a device (its observed :class:`DeviceState`,
sensor samples) into a :class:`PowerEstimate` with an uncertainty. Models are pure:
they do no I/O, never read a clock (``now`` is passed in) and are fed by the caller:

* :meth:`PowerModel.observe` ingests the newest inputs (learning, counter history);
* :meth:`PowerModel.estimate` is a query and never changes the model.

``samples`` maps *role names* (``DeviceConfig.power_model.power_role`` /
``energy_role``) and the well-known keys below to the newest :class:`Sample`.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Final, Protocol, runtime_checkable

from suncore.core.control import DeviceState, PowerEstimate
from suncore.core.devices import PowerModelKind
from suncore.core.signals import Quality, Sample
from suncore.core.units import Dimension, to_base

ROOM_TEMP: Final = "room_temp"
"""Well-known sample key: room temperature in °C (learned model feature, optional)."""
OUTDOOR_TEMP: Final = "outdoor_temp"
"""Well-known sample key: outdoor temperature in °C (learned model feature, optional)."""
HOUSE_POWER: Final = "house_w"
"""Well-known sample key: house load in W, for the grid-balance validation."""

DEFAULT_MEASURED_CADENCE_S: Final = 10.0
"""Assumed update cadence of a power sensor when the composition root does not know better
(a 5 minute ``smartthinq_sensors`` sensor must be configured with 300)."""
STALE_CADENCE_FACTOR: Final = 2.0
"""A sample older than ``cadence * factor`` is stale (same rule as the watchdog)."""
STANDBY_UNCERTAINTY_W: Final = 10.0
"""Uncertainty of "device is off": standby loads of a few W are not modelled."""

Samples = Mapping[str, Sample]


@runtime_checkable
class PowerEstimator(Protocol):
    """Common interface of the four power models (:class:`~suncore.core.devices.PowerModelKind`)."""

    @property
    def kind(self) -> PowerModelKind: ...

    @property
    def inputs(self) -> tuple[str, ...]:
        """Sample keys (roles, well-known keys) the model reads; the caller supplies them."""
        ...

    def estimate(self, now: datetime, state: DeviceState | None, samples: Samples) -> PowerEstimate:
        """The device's current power with uncertainty. Never raises, never mutates."""
        ...

    def observe(self, now: datetime, state: DeviceState | None, samples: Samples) -> None:
        """Ingest the newest inputs (history, learning). Idempotent for repeated samples."""
        ...

    def dump_state(self) -> dict[str, Any]:
        """JSON-serialisable state for ``PowerModelStateStore`` (``{}`` = nothing to keep)."""
        ...

    def load_state(self, data: Mapping[str, Any]) -> None:
        """Restore :meth:`dump_state`; unusable data is ignored (the model stays at its prior)."""
        ...


def finite(value: float | None) -> float | None:
    """``value`` if it is a finite number, else ``None``."""
    if value is None or not math.isfinite(value):
        return None
    return value


def sample_base_value(sample: Sample | None, dimension: Dimension) -> tuple[float | None, Quality]:
    """A sample's value in the base unit of ``dimension`` with its quality.

    Missing sample -> ``(None, UNAVAILABLE)``; a unit of another dimension (or none) ->
    ``(None, INVALID)`` - units are never guessed. A bad-quality sample yields no value.
    """
    if sample is None:
        return None, Quality.UNAVAILABLE
    if sample.quality in (Quality.UNAVAILABLE, Quality.INVALID) or sample.value is None:
        return None, sample.quality if sample.quality is not Quality.OK else Quality.UNAVAILABLE
    if sample.unit is None or sample.unit.dimension is not dimension:
        return None, Quality.INVALID
    value = finite(to_base(sample.value, sample.unit))
    if value is None:
        return None, Quality.INVALID
    return value, sample.quality


def age_s(now: datetime, ts: datetime | None) -> float:
    """Seconds between ``ts`` and ``now`` (never negative; ``0`` for an unknown time)."""
    if ts is None:
        return 0.0
    return max(0.0, (now - ts).total_seconds())


def worst_quality(*qualities: Quality) -> Quality:
    return Quality.worst(qualities)


def state_quality(state: DeviceState | None) -> Quality:
    return Quality.UNAVAILABLE if state is None else state.quality


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def make_estimate(
    value_w: float, uncertainty_w: float, quality: Quality, age: float = 0.0
) -> PowerEstimate:
    """Build a :class:`PowerEstimate`, sanitising NaN/inf and negatives."""
    value = finite(value_w)
    unc = finite(uncertainty_w)
    age_ok = finite(age)
    return PowerEstimate(
        value_w=max(0.0, value) if value is not None else 0.0,
        uncertainty_w=max(0.0, unc) if unc is not None else 0.0,
        quality=quality if value is not None and unc is not None else Quality.INVALID,
        age_s=max(0.0, age_ok) if age_ok is not None else 0.0,
    )


__all__ = [
    "DEFAULT_MEASURED_CADENCE_S",
    "HOUSE_POWER",
    "OUTDOOR_TEMP",
    "ROOM_TEMP",
    "STALE_CADENCE_FACTOR",
    "STANDBY_UNCERTAINTY_W",
    "PowerEstimator",
    "Samples",
    "age_s",
    "clamp",
    "finite",
    "make_estimate",
    "sample_base_value",
    "state_quality",
    "worst_quality",
]
