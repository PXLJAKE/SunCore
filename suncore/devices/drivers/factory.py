# SPDX-License-Identifier: Apache-2.0
"""Pick the driver for a device's control kind."""

from __future__ import annotations

from typing import TYPE_CHECKING

from suncore.core.devices import (
    PHASE6_CONTROLS,
    ChargerControl,
    ContinuousControl,
    DeviceConfig,
    LoadpointControl,
    ModeControl,
    OnOffControl,
    RelaysControl,
    SetpointControl,
    StepsControl,
)
from suncore.devices.drivers.base import EntityDriver, ServiceWriter, StateLookup
from suncore.devices.drivers.charger import ChargerDriver
from suncore.devices.drivers.climate import ClimateDriver
from suncore.devices.drivers.mode import ModeDriver
from suncore.devices.drivers.number import NumberDriver
from suncore.devices.drivers.onoff import OnOffDriver
from suncore.devices.drivers.steps import StepsDriver
from suncore.devices.drivers.unsupported import UnsupportedDriver

if TYPE_CHECKING:
    from suncore.devices.evcc import EvccBus, EvccDriver


def create_driver(  # noqa: PLR0911 - one driver per control kind
    device: DeviceConfig,
    *,
    write: ServiceWriter,
    states: StateLookup,
    evcc: EvccBus | None = None,
) -> EntityDriver | EvccDriver:
    """The actuator for ``device`` (structurally a ``suncore.safety.actuator.Actuator``).

    ``write`` is the single Home Assistant write capability (``ServiceCaller.write``) and
    ``states`` a lookup in the entity cache; the composition root supplies both. ``evcc`` is the
    MQTT side of an evcc loadpoint (PR 6-J; ``None`` = no broker link: the driver reports its
    state unavailable and refuses every write).
    """
    control = device.control
    if isinstance(control, LoadpointControl):
        from suncore.devices.evcc import EvccDriver  # noqa: PLC0415 - avoids a package cycle

        return EvccDriver(device, bus=evcc)
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
    if isinstance(control, ChargerControl):
        return ChargerDriver(device, write=write, states=states)
    if isinstance(control, PHASE6_CONTROLS):
        return UnsupportedDriver(device, write=write, states=states)  # until 6-J/6-K
    msg = f"no driver for control kind {control!r}"  # pragma: no cover - exhaustive
    raise TypeError(msg)  # pragma: no cover


__all__ = ["create_driver"]
