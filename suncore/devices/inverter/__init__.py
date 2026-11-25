# SPDX-License-Identifier: Apache-2.0
"""Inverter limit adapters behind the gate's ``InverterActuator`` protocol (PR 6-G, ADR-0018).

* ``generic_number`` - any ``number`` entity that holds an export limit (W) or an AC output
  limit (%);
* ``kostal_rest`` - the core integration's ``Inverter:ActivePowerLimitation`` number (W,
  disabled by default in HA; its semantics - export or AC output - are ``UNVERIFIED``);
* ``kostal_modbus`` - register 533 "active power set-point 1..100 %" through
  ``modbus.write_register`` (volatile: lost on an inverter reset; whether it limits AC output
  including battery discharge is ``UNVERIFIED``), read back through a modbus sensor.

Like the device drivers they are imported only by ``suncore.safety``, the composition root
``suncore.app`` and the simulator (``tests/safety/test_boundary.py``), and only the gate calls
``apply``, ``release`` and ``keep_alive``. They never decide: X1-X6 are the gate's.
"""

from __future__ import annotations

from suncore.core.config import InverterConfig
from suncore.core.inverter import InverterAdapterSpec
from suncore.devices.drivers.base import ServiceWriter, StateLookup
from suncore.devices.inverter.drivers import (
    InverterDriver,
    ModbusLimitDriver,
    NumberLimitDriver,
)


def create_inverter_driver(
    spec: InverterAdapterSpec,
    config: InverterConfig,
    *,
    write: ServiceWriter,
    states: StateLookup,
) -> InverterDriver:
    """The adapter the configuration and its template name; raises ``ValueError`` when it
    cannot be built (no adapter, no read-back entity, no modbus link) - nothing is written."""
    if config.adapter == "none" or config.capability is None:
        msg = "no inverter adapter is configured"
        raise ValueError(msg)
    if spec.driver != config.adapter:
        msg = f"the template drives {spec.driver}, the configuration names {config.adapter}"
        raise ValueError(msg)
    if spec.capability is not None and spec.capability != config.capability:
        msg = f"the template provides {spec.capability}"
        raise ValueError(msg)
    if config.adapter == "kostal_modbus":
        return ModbusLimitDriver(spec, config, write=write, states=states)
    return NumberLimitDriver(spec, config, write=write, states=states)


__all__ = [
    "InverterDriver",
    "ModbusLimitDriver",
    "NumberLimitDriver",
    "create_inverter_driver",
]
