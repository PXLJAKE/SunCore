# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from typing import Any

from suncore.core.units import Unit
from suncore.ha.candidates import entity_candidates
from suncore.ha.connection import EntityState


def _s(entity_id: str, state: str = "1", **attrs: Any) -> EntityState:
    return EntityState(entity_id, state, attrs)


def test_filters_by_device_class_and_unit() -> None:
    states = [
        _s("sensor.pv", "1200", device_class="power", unit_of_measurement="W", friendly_name="PV"),
        _s("sensor.grid_kw", "-1.2", device_class="power", unit_of_measurement="kW"),
        _s("sensor.soc", "55", device_class="battery", unit_of_measurement="%"),
        _s("sensor.energy", "5", device_class="energy", unit_of_measurement="kWh"),
        _s("sensor.pct_power", "5", device_class="power", unit_of_measurement="%"),
        _s("sensor.mw", "5", device_class="power", unit_of_measurement="MW"),
        _s("sensor.nounit", "5", device_class="power"),
        _s("sensor.soc_w", "5", device_class="battery", unit_of_measurement="W"),
        _s("sensor.plain", "5", unit_of_measurement="W"),
        _s("binary_sensor.x", "on", device_class="power", unit_of_measurement="W"),
        _s("sensor.off", "unavailable", device_class="power", unit_of_measurement="W"),
    ]
    got = entity_candidates(states)
    assert [c.entity_id for c in got] == [
        "sensor.grid_kw",
        "sensor.off",
        "sensor.pv",
        "sensor.soc",
    ]
    by_id = {c.entity_id: c for c in got}
    assert by_id["sensor.pv"].name == "PV"
    assert by_id["sensor.grid_kw"].name == "sensor.grid_kw"
    assert by_id["sensor.grid_kw"].unit is Unit.KW
    assert by_id["sensor.soc"].device_class == "battery"
    assert by_id["sensor.off"].available is False
    assert by_id["sensor.pv"].available is True
