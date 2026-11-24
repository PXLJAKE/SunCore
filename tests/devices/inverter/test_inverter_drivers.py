# SPDX-License-Identifier: Apache-2.0
"""Inverter limit adapters (PR 6-G): generic number, Kostal REST number, Kostal register 533 -
exact service calls, read-back, ranges, release."""

from __future__ import annotations

from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from suncore.core.config import InverterConfig, InverterModbusLink
from suncore.core.decisions import BatteryAction, InverterLimitAction
from suncore.core.inverter import InverterAdapterSpec
from suncore.core.site import EntityRef
from suncore.devices.drivers.base import ActionNotSupportedError, WriteRefusedError
from suncore.devices.inverter import (
    ModbusLimitDriver,
    NumberLimitDriver,
    create_inverter_driver,
)
from suncore.safety.inverter import InverterActuator
from tests.devices.drivers.fakes import FakeHA, intent

GENERIC = InverterAdapterSpec(template_id="generic.inverter_limit", driver="generic_number")
REST = InverterAdapterSpec(
    template_id="kostal.plenticore_power_limit", driver="kostal_rest", capability="export_limit"
)
MODBUS = InverterAdapterSpec(
    template_id="kostal.plenticore_g3_modbus_limit",
    driver="kostal_modbus",
    capability="ac_limit",
    min=1.0,
    max=100.0,
    volatile=True,
    register=533,
)


def config(adapter: str, capability: str, entity: str, **kw: Any) -> InverterConfig:
    return InverterConfig(
        adapter=adapter,
        capability=capability,
        limit_entity=EntityRef(entity_id=entity),
        nominal_ac_w=10_000.0,
        **kw,
    )


def limit(**kw: float) -> Any:
    return intent(InverterLimitAction(**kw), asset="inverter")


def test_the_drivers_are_inverter_actuators() -> None:
    ha = FakeHA()
    driver = create_inverter_driver(
        GENERIC,
        config("generic_number", "export_limit", "number.export_limit"),
        write=ha.write,
        states=ha.lookup,
    )
    assert isinstance(driver, NumberLimitDriver)
    assert isinstance(driver, InverterActuator)


async def test_generic_number_in_kw_writes_and_reads_back_in_watts() -> None:
    ha = FakeHA()
    ha.set("number.export_limit", "13.5", unit_of_measurement="kW", min=0, max=20)
    driver = create_inverter_driver(
        GENERIC,
        config("generic_number", "export_limit", "number.export_limit"),
        write=ha.write,
        states=ha.lookup,
    )
    assert await driver.read_back() == InverterLimitAction(max_export_w=13_500.0)
    assert driver.limit_range() == (0.0, 10_000.0)  # nominal narrows the entity's 20 kW
    await driver.apply(limit(max_export_w=2500.0))
    assert ha.sent == [
        ("number", "set_value", {"entity_id": "number.export_limit"}, {"value": 2.5})
    ]


async def test_generic_number_in_percent_for_an_ac_limit() -> None:
    ha = FakeHA()
    ha.set("number.ac_limit", "100", unit_of_measurement="%")
    driver = create_inverter_driver(
        GENERIC,
        config("generic_number", "ac_limit", "number.ac_limit"),
        write=ha.write,
        states=ha.lookup,
    )
    assert await driver.read_back() == InverterLimitAction(max_ac_pct=100.0)
    await driver.apply(limit(max_ac_pct=40.0))
    assert ha.sent == [("number", "set_value", {"entity_id": "number.ac_limit"}, {"value": 40.0})]


async def test_an_unknown_unit_or_unavailable_entity_reads_nothing() -> None:
    ha = FakeHA()
    ha.set("number.export_limit", "13.5", unit_of_measurement="MW")
    driver = create_inverter_driver(
        GENERIC,
        config("generic_number", "export_limit", "number.export_limit"),
        write=ha.write,
        states=ha.lookup,
    )
    assert await driver.read_back() is None
    assert not driver.readable()
    ha.set("number.export_limit", "unavailable")
    assert await driver.read_back() is None


async def test_kostal_rest_writes_the_active_power_limitation_number() -> None:
    ha = FakeHA()
    entity = "number.scb_inverter_active_power_limitation"
    ha.set(entity, "13500", unit_of_measurement="W")
    driver = create_inverter_driver(
        REST, config("kostal_rest", "export_limit", entity), write=ha.write, states=ha.lookup
    )
    assert driver.heartbeat_s is None
    await driver.apply(limit(max_export_w=0.0))
    await driver.release(InverterLimitAction(max_export_w=13_500.0))
    assert ha.sent == [
        ("number", "set_value", {"entity_id": entity}, {"value": 0.0}),
        ("number", "set_value", {"entity_id": entity}, {"value": 13_500.0}),
    ]
    assert driver.applied is None


async def test_kostal_modbus_writes_register_533_as_a_single_int() -> None:
    ha = FakeHA()
    ha.set("sensor.kostal_533", "100", unit_of_measurement="%")
    driver = create_inverter_driver(
        MODBUS,
        config(
            "kostal_modbus",
            "ac_limit",
            "sensor.kostal_533",
            modbus=InverterModbusLink(hub="kostal", slave=71, heartbeat_s=60),
        ),
        write=ha.write,
        states=ha.lookup,
    )
    assert isinstance(driver, ModbusLimitDriver)
    assert driver.heartbeat_s == 60
    assert driver.limit_range() == (1.0, 100.0)
    await driver.apply(limit(max_ac_pct=37.2))
    await driver.keep_alive()
    await driver.release(None)  # nothing read at takeover: the inverter's default 100 %
    data = {"hub": "kostal", "slave": 71, "address": 533}
    assert ha.sent == [
        ("modbus", "write_register", {}, {**data, "value": 38}),
        ("modbus", "write_register", {}, {**data, "value": 38}),
        ("modbus", "write_register", {}, {**data, "value": 100}),
    ]
    assert all(isinstance(c.service_data["value"], int) for c in ha.calls if c.service_data)


@given(st.floats(min_value=0, max_value=100, allow_nan=False))
async def test_register_533_never_gets_less_than_asked_nor_outside_1_to_100(pct: float) -> None:
    ha = FakeHA()
    driver = create_inverter_driver(
        MODBUS,
        config(
            "kostal_modbus", "ac_limit", "sensor.kostal_533", modbus=InverterModbusLink(slave=71)
        ),
        write=ha.write,
        states=ha.lookup,
    )
    await driver.apply(limit(max_ac_pct=pct))
    assert ha.calls[0].service_data is not None
    value = ha.calls[0].service_data["value"]
    assert 1 <= value <= 100
    assert value + 1e-9 >= pct


async def test_keep_alive_without_a_held_limit_writes_nothing() -> None:
    ha = FakeHA()
    driver = create_inverter_driver(
        MODBUS,
        config(
            "kostal_modbus", "ac_limit", "sensor.kostal_533", modbus=InverterModbusLink(slave=71)
        ),
        write=ha.write,
        states=ha.lookup,
    )
    await driver.keep_alive()
    assert ha.calls == []


async def test_the_wrong_action_or_unit_is_refused() -> None:
    ha = FakeHA()
    driver = create_inverter_driver(
        GENERIC,
        config("generic_number", "export_limit", "number.export_limit"),
        write=ha.write,
        states=ha.lookup,
    )
    with pytest.raises(ActionNotSupportedError):
        await driver.apply(intent(BatteryAction(max_charge_w=0.0), asset="inverter"))
    with pytest.raises(WriteRefusedError):
        await driver.apply(limit(max_ac_pct=50.0))
    with pytest.raises(WriteRefusedError):
        await driver.release(None)  # a number without a known baseline: never guess
    assert ha.calls == []


def test_a_driver_is_not_built_without_what_it_needs() -> None:
    ha = FakeHA()
    with pytest.raises(ValueError, match="no inverter adapter"):
        create_inverter_driver(GENERIC, InverterConfig(), write=ha.write, states=ha.lookup)
    with pytest.raises(ValueError, match="template drives"):
        create_inverter_driver(
            REST,
            config("generic_number", "export_limit", "number.x"),
            write=ha.write,
            states=ha.lookup,
        )
    with pytest.raises(ValueError, match="modbus"):
        create_inverter_driver(
            MODBUS,
            config("kostal_modbus", "ac_limit", "sensor.kostal_533"),
            write=ha.write,
            states=ha.lookup,
        )
    with pytest.raises(ValueError, match="read-back"):
        create_inverter_driver(
            REST,
            InverterConfig(adapter="kostal_rest", capability="export_limit"),
            write=ha.write,
            states=ha.lookup,
        )
