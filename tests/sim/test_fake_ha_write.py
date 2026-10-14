# SPDX-License-Identifier: Apache-2.0
"""The fake Home Assistant's write path: services, errors, contexts, faults (Phase 3 WP8)."""

from __future__ import annotations

import itertools
from typing import Any

import pytest

from suncore.ha.connection import (
    CommandError,
    ConnectionState,
    EntityUpdate,
    NotConnectedError,
    ServiceCallerProvider,
    StateContext,
)
from suncore.sim.fake_ha import SUPERVISOR_USER_ID, USER_ID
from tests.sim.helpers import Pump, scenario_from

pytestmark = pytest.mark.simulator

BASE = """
name: write
start: "2026-07-15T12:00:00+02:00"
duration_s: 7200
step_s: 5
house: {{battery: {{capacity_wh: 0}}}}
outdoor: {{mean_c: 30, amplitude_c: 0}}
{extra}
devices:
  - id: ac
    physics:
      {{kind: climate, initial_mode: "off", initial_setpoint_c: 24, initial_temp_c: 27,
        default_on_mode: cool, power_sensor: true, power_poll_s: 60}}
  - id: pump
    physics: {{kind: switch, nominal_w: 750, power_sensor: true}}
  - id: flag
    physics: {{kind: switch, entity_id: "input_boolean.flag", nominal_w: 100}}
  - id: rod
    physics: {{kind: number, max: 3000, step: 100}}
  - id: boiler
    physics: {{kind: water_heater, variant: select}}
  - id: tank
    physics: {{kind: water_heater, variant: entity, levels: [["off", 0], ["eco", 1000]]}}
"""


def scenario(extra: str = ""):  # type: ignore[no-untyped-def]
    return scenario_from(BASE.format(extra=extra))


class Recorder:
    def __init__(self, pump: Pump) -> None:
        self.updates: list[EntityUpdate] = []
        pump.conn.add_entity_listener(self.updates.append)

    def states(self, entity_id: str) -> list:  # type: ignore[type-arg]
        return [u.changed[entity_id] for u in self.updates if entity_id in u.changed and not u.full]


async def start(extra: str = "") -> tuple[Pump, Recorder]:
    pump = Pump(scenario(extra))
    rec = Recorder(pump)
    await pump.start()
    return pump, rec


def test_the_fake_offers_the_write_capability() -> None:
    from suncore.sim.fake_ha import FakeHAConnection  # noqa: PLC0415

    assert issubclass(FakeHAConnection, ServiceCallerProvider)


async def test_a_switch_call_returns_the_context_and_the_state_carries_it() -> None:
    pump, rec = await start()
    result, took = await pump.call("switch", "turn_on", "switch.sim_pump")
    assert result.context_id is not None
    assert took == pytest.approx(0.1, abs=0.15)
    changed = rec.states("switch.sim_pump")
    assert [s.state for s in changed] == ["on"]
    # ours: the context id of the call, the Supervisor as user (a call through the proxy)
    assert changed[0].context == StateContext(result.context_id, user_id=SUPERVISOR_USER_ID)
    assert pump.state("sensor.sim_pump_power").state == "750.0"
    assert pump.conn.call_log[-1].reached == ("switch.sim_pump",)
    assert pump.conn.call_log[-1].error is None


async def test_input_boolean_number_and_select_services() -> None:
    pump, _ = await start()
    await pump.call("input_boolean", "turn_on", "input_boolean.flag")
    assert pump.state("input_boolean.flag").state == "on"
    await pump.call("number", "set_value", "number.sim_rod", value=1200)
    assert float(pump.state("number.sim_rod").state) == 1200.0
    await pump.call("select", "select_option", "select.sim_boiler_level", option="high")
    assert pump.state("select.sim_boiler_level").state == "high"
    assert pump.conn.demo.devices_power_w() == pytest.approx(100 + 1200 + 3000 + 5)


async def test_water_heater_services() -> None:
    pump, _ = await start()
    await pump.call(
        "water_heater", "set_operation_mode", "water_heater.sim_tank", operation_mode="eco"
    )
    assert pump.state("water_heater.sim_tank").state == "eco"
    await pump.call("water_heater", "turn_off", "water_heater.sim_tank")
    assert pump.state("water_heater.sim_tank").state == "off"
    await pump.call("water_heater", "set_temperature", "water_heater.sim_tank", temperature=52)
    assert pump.state("water_heater.sim_tank").attributes["temperature"] == 52.0


@pytest.mark.parametrize(
    "case",
    [
        (
            "number",
            "set_value",
            "number.sim_rod",
            {"value": 9000},
            "service_validation_error",
            "outside valid range",
        ),
        (
            "select",
            "select_option",
            "select.sim_boiler_level",
            {"option": "x"},
            "service_validation_error",
            "not valid",
        ),
        ("number", "set_value", "number.sim_rod", {}, "invalid_format", "required key"),
        ("number", "go", "number.sim_rod", {}, "not_found", "Service number.go not found."),
        (
            "climate",
            "set_hvac_mode",
            "climate.sim_ac",
            {"hvac_mode": "sauna"},
            "invalid_format",
            "HVACMode",
        ),
        (
            "climate",
            "set_temperature",
            "climate.sim_ac",
            {"temperature": 99},
            "service_validation_error",
            "Accepted range",
        ),
    ],
)
async def test_errors_have_homeassistants_codes_and_nothing_changes(
    case: tuple[str, str, str, dict[str, Any], str, str],
) -> None:
    domain, service, entity, data, code, text = case
    pump, rec = await start()
    before = len(rec.updates)
    with pytest.raises(CommandError) as excinfo:
        await pump.call(domain, service, entity, **data)
    assert excinfo.value.code == code
    assert text in excinfo.value.message
    assert len(rec.updates) == before
    record = pump.conn.call_log[-1]
    assert record.error == code
    assert record.context_id is None


async def test_a_missing_target_is_a_schema_error() -> None:
    pump, _ = await start()
    with pytest.raises(CommandError) as excinfo:
        await pump.caller.write("switch", "turn_on")
    assert excinfo.value.code == "invalid_format"
    assert "at least one of entity_id" in excinfo.value.message


async def test_an_unknown_or_unavailable_entity_succeeds_without_effect() -> None:
    pump, rec = await start(
        'faults:\n  - {kind: unavailable, target: "device:pump", from_s: 0, to_s: 600}'
    )
    await pump.advance(1)
    pump.conn.advance()  # the fault window opens with the first house step
    await pump.vt.settle()
    before = len(rec.updates)
    # a switch that does not exist: success, nothing happens (research: a result proves nothing)
    result, _ = await pump.call("switch", "turn_on", "switch.does_not_exist")
    assert result.context_id is not None
    assert pump.conn.call_log[-1].reached == ()
    assert len(rec.updates) == before
    # a switch that is unavailable: same
    assert pump.state("switch.sim_pump").state == "unavailable"
    await pump.call("switch", "turn_on", "switch.sim_pump")
    assert pump.conn.call_log[-1].reached == ()
    assert not pump.conn.demo.devices.devices["pump"].is_on()
    # an entity of another domain is not part of the service
    await pump.call("climate", "turn_off", "switch.sim_pump")
    assert pump.conn.call_log[-1].reached == ()


async def test_a_unit_that_reports_off_is_switched_on_by_a_set_point_after_the_sleep() -> None:
    pump, rec = await start()
    result, took = await pump.call("climate", "set_temperature", "climate.sim_ac", temperature=22.9)
    assert took == pytest.approx(2.0 + 0.3, abs=0.2)  # the lg_thinq sleep(2) after POWER_ON
    await pump.advance(5)
    states = rec.states("climate.sim_ac")
    assert states[-1].state == "cool"
    assert states[-1].attributes["temperature"] == 22.0  # int() truncation, not 22.9
    # the push arrived within 5 s of the call: our context
    assert states[-1].context is not None
    assert states[-1].context.id == result.context_id
    assert pump.conn.demo.devices.devices["ac"].mode == "cool"


async def test_a_slow_push_carries_a_fresh_id_only_context() -> None:
    extra = "devices_note: unused"
    del extra
    pump = Pump(
        scenario_from(
            BASE.format(extra="").replace(
                "default_on_mode: cool,", "default_on_mode: cool, push_delay_s: 9,"
            )
        )
    )
    rec = Recorder(pump)
    await pump.start()
    result, _ = await pump.call("climate", "turn_on", "climate.sim_ac")
    await pump.advance(15)
    state = rec.states("climate.sim_ac")[-1]
    assert state.state == "cool"
    assert state.context is not None
    assert state.context.id != result.context_id  # more than 5 s later: HA drops our context
    assert state.context.user_id is None  # an integration push: indistinguishable from a remote


async def test_state_arrives_by_delayed_push_not_at_the_call() -> None:
    pump, rec = await start()
    await pump.call("climate", "turn_on", "climate.sim_ac")
    await pump.advance(1.5)  # the command reached the unit: it runs ...
    assert pump.conn.demo.devices.devices["ac"].mode == "cool"
    assert pump.state("climate.sim_ac").state == "off"  # ... but Home Assistant does not know yet
    await pump.advance(5)
    assert pump.state("climate.sim_ac").state == "cool"
    assert rec.states("climate.sim_ac")[-1].state == "cool"


async def test_manual_remote_and_ui_changes() -> None:
    extra = """manual:
  - {at_s: 10, device: pump, action: turn_on, source: remote}
  - {at_s: 20, device: rod, action: set_value, value: 700, source: ui}
  - {at_s: 30, device: ac, action: turn_on, source: remote}"""
    pump, rec = await start(extra)
    await pump.advance(40)
    remote = rec.states("switch.sim_pump")[-1]
    assert remote.state == "on"
    assert remote.context is not None
    assert remote.context.user_id is None  # a remote control: id-only
    ui = rec.states("number.sim_rod")[-1]
    assert float(ui.state) == 700.0
    assert ui.context is not None
    assert ui.context.user_id == USER_ID
    assert rec.states("climate.sim_ac")[-1].state == "cool"  # reported 3 s after the key press


async def test_unavailable_stale_and_frozen_faults() -> None:
    extra = """faults:
  - {kind: unavailable, target: "device:rod", from_s: 100, to_s: 200}
  - {kind: stale, target: sensor.sim_pump_power, from_s: 100, to_s: 200}
  - {kind: frozen, target: pv_power, from_s: 300, to_s: 600}"""
    pump, rec = await start(extra)
    await pump.call("switch", "turn_on", "switch.sim_pump")
    pump.conn.demo.devices.devices["pump"].report()
    for _ in range(30):  # 150 s
        await pump.advance(5)
        pump.conn.advance()
        await pump.vt.settle()
    assert pump.state("number.sim_rod").state == "unavailable"
    power_updates = rec.states("sensor.sim_pump_power")
    # stale: not updated and not emitted during the window (a heartbeat before it was the last)
    assert all(
        s.last_updated is not None and s.last_updated < pump.vt.start.timestamp() + 100 + 1
        for s in power_updates
    )
    for _ in range(40):  # to 350 s
        await pump.advance(5)
        pump.conn.advance()
        await pump.vt.settle()
    assert pump.state("number.sim_rod").state != "unavailable"
    first = pump.state("sensor.sim_pv_power").state
    for _ in range(20):
        await pump.advance(5)
        pump.conn.advance()
        await pump.vt.settle()
    frozen = rec.states("sensor.sim_pv_power")
    assert frozen[-1].state == first  # reported again and again, never changing
    assert frozen[-1].last_updated == pump.vt.now.timestamp()


async def test_writes_fail_while_disconnected_and_the_snapshot_follows() -> None:
    pump, rec = await start("faults:\n  - {kind: disconnect, from_s: 20, to_s: 60}")
    states: list[ConnectionState] = []
    pump.conn.add_state_listener(states.append)
    for _ in range(5):
        await pump.advance(5)
        pump.conn.advance()
        await pump.vt.settle()
    assert pump.conn.state is ConnectionState.RECONNECTING
    with pytest.raises(NotConnectedError):
        await pump.call("switch", "turn_on", "switch.sim_pump")
    assert pump.conn.call_log[-1].error == "not_connected"
    for _ in range(10):
        await pump.advance(5)
        pump.conn.advance()
        await pump.vt.settle()
    assert states == [ConnectionState.RECONNECTING, ConnectionState.CONNECTED]
    assert sum(u.full for u in rec.updates) == 2  # the initial snapshot and the one on reconnect


async def test_the_polled_sensor_reports_its_cadence_even_when_unchanged() -> None:
    pump, rec = await start()
    for _ in range(40):  # 200 s at a constant standby power
        await pump.advance(5)
        pump.conn.advance()
        await pump.vt.settle()
    power = rec.states("sensor.sim_ac_power")
    assert 2 <= len(power) <= 5
    assert {s.state for s in power} == {"5"}  # unchanged value, re-reported every poll period
    gaps = [b.last_updated - a.last_updated for a, b in itertools.pairwise(power)]
    assert all(55 <= g <= 70 for g in gaps if g is not None)


async def test_the_house_balance_includes_the_device_draw() -> None:
    pump, _ = await start()
    await pump.call("number", "set_value", "number.sim_rod", value=2000)
    await pump.call("select", "select_option", "select.sim_boiler_level", option="low")
    await pump.advance(2)
    pump.conn.advance()
    house = pump.conn._house_state
    assert house is not None
    assert house.devices_w == pytest.approx(2000 + 1000 + 5, abs=1)
    assert house.load_w >= house.devices_w
    assert house.grid_w == pytest.approx(house.load_w + house.battery_w - house.pv_w)
