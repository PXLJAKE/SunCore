# SPDX-License-Identifier: Apache-2.0
"""Entity candidates for the settings picker, derived from a ``get_states`` result.

Only the state machine is used (no registry commands - their names are ``UNVERIFIED``,
see ``docs/research/ha-websocket-api.md``). A candidate is a ``sensor`` whose
``device_class`` is ``power`` (unit W or kW) or ``battery`` (unit %). ``device_class`` and
``unit_of_measurement`` are standard sensor state attributes.

Sensors without a ``device_class`` (template sensors, some integrations) are
candidates too when their unit is W, kW or %; a ``device_class`` that contradicts the
unit (e.g. ``humidity`` in %) excludes the sensor.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from suncore.core.units import Unit, parse_unit
from suncore.ha.cache import UNAVAILABLE_STATES
from suncore.ha.connection import EntityState

_ALLOWED: dict[str, frozenset[Unit]] = {
    "power": frozenset({Unit.W, Unit.KW}),
    "battery": frozenset({Unit.PERCENT}),
}


@dataclass(frozen=True, slots=True)
class EntityCandidate:
    entity_id: str
    name: str
    device_class: str
    unit: Unit
    state: str
    available: bool


_CLASS_FOR_UNIT: dict[Unit, str] = {Unit.W: "power", Unit.KW: "power", Unit.PERCENT: "battery"}


def entity_candidates(states: Iterable[EntityState]) -> list[EntityCandidate]:
    """Power and battery sensors, sorted by entity id."""
    result: list[EntityCandidate] = []
    for s in states:
        if not s.entity_id.startswith("sensor."):
            continue
        raw_class = s.attributes.get("device_class")
        unit_text = s.attributes.get("unit_of_measurement")
        unit = parse_unit(unit_text if isinstance(unit_text, str) else None)
        if unit is None or unit not in _CLASS_FOR_UNIT:
            continue
        if raw_class is None:
            device_class = _CLASS_FOR_UNIT[unit]
        elif isinstance(raw_class, str) and raw_class in _ALLOWED:
            device_class = raw_class
        else:
            continue
        if unit not in _ALLOWED[device_class]:
            continue
        friendly = s.attributes.get("friendly_name")
        result.append(
            EntityCandidate(
                entity_id=s.entity_id,
                name=friendly if isinstance(friendly, str) and friendly else s.entity_id,
                device_class=device_class,
                unit=unit,
                state=s.state,
                available=s.state not in UNAVAILABLE_STATES,
            )
        )
    return sorted(result, key=lambda c: c.entity_id)
