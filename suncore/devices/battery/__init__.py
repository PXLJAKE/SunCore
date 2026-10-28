# SPDX-License-Identifier: Apache-2.0
"""Battery adapters behind the gate's ``BatteryActuator`` protocol (Phase 4 WP10, ADR-0013).

``generic`` (template ``controls``), ``kostal_plenticore`` (the core integration's min-SoC
number) and ``kostal_modbus`` (§3.5 limits through ``modbus.write_register``). Like the device
drivers they are imported only by ``suncore.safety``, the composition root ``suncore.app`` and
the simulator (``tests/safety/test_boundary.py``), and only the gate calls ``apply``,
``release``, ``arm_fallback`` and ``keep_alive``.
"""

from __future__ import annotations

from suncore.core.battery import BatterySettings
from suncore.core.battery_adapter import BatteryAdapterSpec
from suncore.devices.battery.base import BatteryDriver
from suncore.devices.battery.generic import GenericBatteryDriver
from suncore.devices.battery.kostal_modbus import KostalModbusDriver
from suncore.devices.battery.kostal_plenticore import KostalPlenticoreDriver
from suncore.devices.drivers.base import ServiceWriter, StateLookup


def create_battery_driver(
    spec: BatteryAdapterSpec,
    settings: BatterySettings,
    *,
    write: ServiceWriter,
    states: StateLookup,
) -> BatteryDriver:
    """The adapter the template's ``driver`` names (``BatteryTemplate.adapter_spec()``); raises
    ``ValueError`` when it cannot be built (no model, unmapped roles, no modbus link) - the battery
    then stays observe-only."""
    if settings.adapter_template_id != spec.template_id:
        msg = "the battery settings name another adapter template"
        raise ValueError(msg)
    if spec.driver == "kostal_plenticore":
        return KostalPlenticoreDriver(spec, settings, write=write, states=states)
    if spec.driver == "kostal_modbus":
        return KostalModbusDriver(spec, settings, write=write, states=states)
    return GenericBatteryDriver(spec, settings, write=write, states=states)


__all__ = [
    "BatteryDriver",
    "GenericBatteryDriver",
    "KostalModbusDriver",
    "KostalPlenticoreDriver",
    "create_battery_driver",
]
