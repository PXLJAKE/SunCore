# SPDX-License-Identifier: Apache-2.0
"""Per-entity update cadences of the sensors that feed the power models.

Why. ``subscribe_entities`` delivers a ``state_changed`` only when the state or its attributes
change, and carries no ``last_reported`` (research/ha-websocket-api.md "last_reported"). A
sensor that keeps reporting the same value is silent on the stream; the *only* thing that
refreshes the entity cache for it is the client's periodic ``get_states`` resync
(:data:`~suncore.ha.websocket.DEFAULT_RESYNC_INTERVAL_S`, 60 s), which compares ``last_reported``.
So the real freshness of an idle sensor is the resync, and a sensor without a known cadence must be
judged against *that*, not against the 2 s of a busy one. A second periodic ``get_states`` only for
slow entities would repeat the resync and is therefore not added; the resync already is the cheap
refresh.

The rules (``UNVERIFIED`` defaults where nothing is configured; templates and the device editor
should state the real period):

* the power sensor (``power_model.power_role``): ``power_model.power_update_s`` if set, else
  :data:`IDLE_REFRESH_S` - the resync period;
* the energy counter (``power_model.energy_role``): ``power_model.counter_update_s`` if set, else
  :data:`DEFAULT_COUNTER_CADENCE_S`;
* the tank probe of a thermal device (``temperature`` role, ``core.control.heat_capped``):
  :data:`IDLE_REFRESH_S` - it gates heating, so a quiet probe must not look stale before the
  resync could refresh it (stale = no more heat);
* every other entity of a device (control entities, daily counters such as ``energy_yesterday``)
  gets no cadence: the control layer only watches what the power model reads
  (``suncore.app.control``), and a control entity has no staleness (it is a state, not a sample).

A cadence of ``c`` seconds means: stale after ``c * stale_cadence_factor`` (2) without proof of
life, in the cache's maximum age and in the watchdog.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Final

from suncore.core.control import TEMPERATURE_ROLE, has_heat_limit
from suncore.core.devices import DeviceConfig
from suncore.ha.websocket import DEFAULT_RESYNC_INTERVAL_S

IDLE_REFRESH_S: Final[float] = DEFAULT_RESYNC_INTERVAL_S
"""Freshness floor of any sensor: an unchanged value is only seen by the resync."""
DEFAULT_COUNTER_CADENCE_S: Final = 300.0
"""Energy counter whose update period is not configured (``UNVERIFIED`` default)."""


def power_cadence_s(device: DeviceConfig) -> float | None:
    """Update period of the device's power sensor; ``None`` if the model has none."""
    pm = device.power_model
    if pm.power_role is None or pm.power_role not in device.entities:
        return None
    return float(pm.power_update_s) if pm.power_update_s is not None else IDLE_REFRESH_S


def counter_cadence_s(device: DeviceConfig) -> float | None:
    """Update period of the device's energy counter; ``None`` if the model has none."""
    pm = device.power_model
    if pm.energy_role is None or pm.energy_role not in device.entities:
        return None
    return (
        float(pm.counter_update_s) if pm.counter_update_s is not None else DEFAULT_COUNTER_CADENCE_S
    )


def device_cadences(devices: Iterable[DeviceConfig]) -> dict[str, float]:
    """Entity id -> cadence in seconds for the sensors of ``devices`` (see the module docstring)."""
    result: dict[str, float] = {}
    for device in devices:
        pm = device.power_model
        power = power_cadence_s(device)
        if power is not None and pm.power_role is not None:
            result[device.entities[pm.power_role].entity_id] = power
        if has_heat_limit(device):
            # The tank probe changes slowly: an unchanged reading is refreshed by the resync.
            result[device.entities[TEMPERATURE_ROLE].entity_id] = IDLE_REFRESH_S
        counter = counter_cadence_s(device)
        if counter is not None and pm.energy_role is not None:
            result[device.entities[pm.energy_role].entity_id] = counter
    return result


def merge_cadences(*sources: Mapping[str, float]) -> dict[str, float]:
    """Later sources override earlier ones (explicit knowledge, e.g. the simulator's)."""
    merged: dict[str, float] = {}
    for source in sources:
        merged.update(source)
    return merged


__all__ = [
    "DEFAULT_COUNTER_CADENCE_S",
    "IDLE_REFRESH_S",
    "counter_cadence_s",
    "device_cadences",
    "merge_cadences",
    "power_cadence_s",
]
