# SPDX-License-Identifier: Apache-2.0
"""Pick the driver for a device's control kind."""

from __future__ import annotations

from suncore.core.devices import (
    ContinuousControl,
    DeviceConfig,
    ModeControl,
    OnOffControl,
    RelaysControl,
    SetpointControl,
    StepsControl,
)
from suncore.devices.drivers.base import EntityDriver, ServiceWriter, StateLookup
from suncore.devices.drivers.climate import ClimateDriver
from suncore.devices.drivers.mode import ModeDriver
from suncore.devices.drivers.number import NumberDriver
from suncore.devices.drivers.onoff import OnOffDriver
from suncore.devices.drivers.steps import StepsDriver


def create_driver(
    device: DeviceConfig, *, write: ServiceWriter, states: StateLookup
) -> EntityDriver:
    """The actuator for ``device`` (structurally a ``suncore.safety.actuator.Actuator``).

    ``write`` is the single Home Assistant write capability (``ServiceCaller.write``) and
    ``states`` a lookup in the entity cache; the composition root supplies both.
    """
    control = device.control
    if isinstance(control, OnOffControl):
        return OnOffDriver(device, write=write, states=states)
    if isinstance(control, SetpointControl):
        return ClimateDriver(device, write=write, states=states)
    if isinstance(control, StepsControl | RelaysControl):
        return StepsDriver(device, write=write, states=states)
    if isinstance(control, ContinuousControl):
        return NumberDriver(device, write=write, states=states)
    if isinstance(control, ModeControl):
        return ModeDriver(device, write=write, states=states)
    msg = f"no driver for control kind {control!r}"  # pragma: no cover - exhaustive
    raise TypeError(msg)  # pragma: no cover


__all__ = ["create_driver"]
