# SPDX-License-Identifier: Apache-2.0
"""Device drivers (Phase 3 WP6): dumb actuators over Home Assistant entities.

Importable only by ``suncore.safety`` and the composition root ``suncore.app``
(``tests/safety/test_boundary.py``); only ``safety.gate`` calls ``apply``/``release``.
"""

from suncore.devices.drivers.base import (
    ActionNotSupportedError,
    DriverError,
    EntityDriver,
    EntityStateView,
    ServiceWriter,
    StateLookup,
    WriteRefusedError,
    WriteResult,
)
from suncore.devices.drivers.climate import ClimateDriver
from suncore.devices.drivers.factory import create_driver
from suncore.devices.drivers.mode import ModeDriver
from suncore.devices.drivers.number import NumberDriver
from suncore.devices.drivers.onoff import OnOffDriver
from suncore.devices.drivers.steps import StepsDriver

__all__ = [
    "ActionNotSupportedError",
    "ClimateDriver",
    "DriverError",
    "EntityDriver",
    "EntityStateView",
    "ModeDriver",
    "NumberDriver",
    "OnOffDriver",
    "ServiceWriter",
    "StateLookup",
    "StepsDriver",
    "WriteRefusedError",
    "WriteResult",
    "create_driver",
]
