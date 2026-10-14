# SPDX-License-Identifier: Apache-2.0
"""Factory: ``DeviceConfig.power_model`` -> a power model instance."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from suncore.core.devices import DeviceConfig, PowerModelKind
from suncore.devices.power.common import DEFAULT_MEASURED_CADENCE_S, PowerEstimator
from suncore.devices.power.counter import CounterDerivedModel
from suncore.devices.power.learned import LearnedPowerModel
from suncore.devices.power.measured import MeasuredPowerModel
from suncore.devices.power.nominal import NominalPowerModel


def create_power_model(
    device: DeviceConfig, *, power_cadence_s: float | None = None
) -> PowerEstimator:
    """Build the power model a device configures.

    ``power_cadence_s`` is the expected update cadence of the device's power sensor
    (300 for the polled ``smartthinq_sensors`` sensor); the composition root knows it
    per entity, the device config does not. Counters take their cadence from
    ``power_model.counter_update_s``. Samples are keyed by the power model's roles; see
    :attr:`PowerEstimator.inputs`.
    """
    pm = device.power_model
    cadence = power_cadence_s if power_cadence_s is not None else DEFAULT_MEASURED_CADENCE_S
    kind = pm.kind
    if kind is PowerModelKind.MEASURED:
        assert pm.power_role is not None  # validated by PowerModel
        return MeasuredPowerModel(pm.power_role, cadence_s=cadence, nominal_w=pm.nominal_w)
    if kind is PowerModelKind.COUNTER_DERIVED:
        assert pm.energy_role is not None
        return CounterDerivedModel(
            pm.energy_role, update_s=pm.counter_update_s, nominal_w=pm.nominal_w
        )
    if kind is PowerModelKind.LEARNED:
        assert pm.nominal_w is not None
        return LearnedPowerModel(
            pm.nominal_w,
            power_role=pm.power_role,
            energy_role=pm.energy_role,
            counter_update_s=pm.counter_update_s,
            cadence_s=power_cadence_s,
        )
    return NominalPowerModel(pm.nominal_w, device.control)


def restore_power_model(model: PowerEstimator, stored: Mapping[str, Any] | None) -> None:
    """Load a stored state (``PowerModelStateStore.get``) into ``model``; ``None`` is a no-op."""
    if stored:
        model.load_state(stored)


__all__ = ["create_power_model", "restore_power_model"]
