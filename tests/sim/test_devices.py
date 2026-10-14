# SPDX-License-Identifier: Apache-2.0
"""Physics and entity behaviour of the simulated devices (Phase 3 WP8), without Home Assistant."""

from __future__ import annotations

import itertools
from datetime import UTC, datetime, timedelta, timezone

import pytest

from suncore.sim.devices import (
    ClimateParams,
    HourlyCounter,
    NumberParams,
    ServiceError,
    SimClimate,
    SimNumberLoad,
    SimSwitchLoad,
    SimWaterHeater,
    SwitchParams,
    WaterHeaterParams,
    build_device_set,
    validate_schema,
)

pytestmark = pytest.mark.simulator

T0 = datetime(2026, 7, 15, 12, 0, tzinfo=timezone(timedelta(hours=2)))


def run(
    device: SimClimate | SimWaterHeater, start: datetime, seconds: float, outdoor: float
) -> datetime:
    """Advance a device in 30 s calls (like the house step) and return the end time."""
    device.advance(start, outdoor)
    t = start
    while (t - start).total_seconds() < seconds:
        t += timedelta(seconds=30)
        device.advance(t, outdoor)
    return t


def climate(**kw: object) -> SimClimate:
    params = ClimateParams(**{"initial_mode": "heat", "initial_setpoint_c": 21.0, **kw})  # type: ignore[arg-type]
    return SimClimate("ac", params)


# --- climate physics -------------------------------------------------------------------


def test_a_heating_unit_warms_the_room_to_the_set_point_and_holds_it() -> None:
    ac = climate(initial_temp_c=17.0, ua_w_per_k=150.0)
    t = run(ac, T0, 6 * 3600, outdoor=5.0)
    assert ac.temp_c == pytest.approx(21.0, abs=0.5)  # a proportional unit holds a little below
    held = [ac.power_w()]
    for _ in range(40):
        t += timedelta(minutes=5)
        ac.advance(t, 5.0)
        held.append(ac.power_w())
    # steady state: far below flat out, between the minimum and the nominal power
    assert max(held) < ac.p.nominal_w
    assert min(held) >= ac.p.idle_w


def test_the_power_follows_the_temperature_error() -> None:
    """A set-point shift makes the compressor run flat out (the controller assumes that)."""
    ac = climate(initial_temp_c=21.0, ua_w_per_k=150.0)
    t = run(ac, T0, 3 * 3600, outdoor=5.0)
    base_w = ac.power_w()
    ac._set_target(23.0)
    ac.advance(t + timedelta(seconds=30), 5.0)
    assert ac.power_w() == pytest.approx(ac.p.nominal_w)
    assert ac.power_w() > base_w + 100
    # once the room reached the new set-point the power falls back
    run(ac, t + timedelta(seconds=30), 8 * 3600, outdoor=5.0)
    assert ac.temp_c > 22.0
    assert ac.power_w() < ac.p.nominal_w


def test_modes_draw_standby_idle_fan_or_compressor_power() -> None:
    off = climate(initial_mode="off", initial_temp_c=30.0)
    run(off, T0, 120, 30.0)
    assert off.power_w() == off.p.standby_w
    assert not off.is_on()
    fan = climate(initial_mode="fan_only", initial_temp_c=30.0)
    run(fan, T0, 120, 30.0)
    assert fan.power_w() == fan.p.fan_w
    cool = climate(initial_mode="cool", initial_setpoint_c=24.0, initial_temp_c=28.0)
    run(cool, T0, 120, 30.0)
    assert cool.power_w() > cool.p.min_w
    assert cool.temp_c < 28.0  # cooling lowers the temperature
    idle = climate(
        initial_mode="cool", initial_setpoint_c=24.0, initial_temp_c=22.0, hysteresis_c=0.5
    )
    run(idle, T0, 120, 20.0)
    assert idle.power_w() == idle.p.idle_w  # compressor stopped: below the set-point


def test_auto_cools_in_summer_and_heats_in_winter() -> None:
    summer = climate(initial_mode="auto", initial_temp_c=26.0, initial_setpoint_c=22.0)
    run(summer, T0, 600, 30.0)
    assert summer.temp_c < 26.0
    winter = climate(initial_mode="auto", initial_temp_c=18.0, initial_setpoint_c=22.0)
    run(winter, T0, 600, 0.0)
    assert winter.temp_c > 18.0


def test_physics_is_deterministic_and_independent_of_the_call_granularity() -> None:
    a, b = climate(initial_temp_c=18.0), climate(initial_temp_c=18.0)
    a.advance(T0, 5.0)
    b.advance(T0, 5.0)
    a.advance(T0 + timedelta(minutes=30), 5.0)
    t = T0
    for _ in range(180):
        t += timedelta(seconds=10)
        b.advance(t, 5.0)
    assert a.temp_c == pytest.approx(b.temp_c, abs=1e-9)
    assert a.counter is not None
    assert b.counter is not None
    assert a.counter.total_wh == pytest.approx(b.counter.total_wh, rel=1e-9)


# --- the LG counter --------------------------------------------------------------------


def test_the_counter_is_integer_hourly_and_resets_at_local_midnight() -> None:
    ac = climate(initial_mode="cool", initial_temp_c=28.0, initial_setpoint_c=24.0)
    start = datetime(2026, 7, 15, 22, 30, tzinfo=timezone(timedelta(hours=2)))
    ac.advance(start, 30.0)
    seen: list[tuple[datetime, int, int]] = []
    t = start
    for _ in range(4 * 120):
        t += timedelta(seconds=30)
        ac.advance(t, 30.0)
        assert ac.counter is not None
        seen.append((t, ac.counter.today, ac.counter.yesterday))
    changes = [(b[0], b[1]) for a, b in itertools.pairwise(seen) if a[1] != b[1]]
    # the published value only changes on a full local hour
    assert changes
    assert all(when.minute == 0 and when.second == 0 for when, _ in changes)
    midnight = datetime(2026, 7, 16, 0, 0, tzinfo=timezone(timedelta(hours=2)))
    before = next(s for s in seen if s[0] == midnight - timedelta(seconds=30))
    at = next(s for s in seen if s[0] == midnight)
    assert before[1] > 0
    assert at[1] == 0  # reset
    assert at[2] > 0  # yesterday's final total
    states = {s.entity_id: s for s in ac.states(t)}
    today = states["sensor.sim_ac_energy_today"]
    assert today.attributes["unit_of_measurement"] == "Wh"
    assert today.attributes["state_class"] == "total"
    assert today.state == str(ac.counter.today)  # type: ignore[union-attr]
    assert int(states["sensor.sim_ac_energy_yesterday"].state) == at[2]


def test_the_counter_truncates_like_lg() -> None:
    counter = HourlyCounter()
    counter.add(1000.0, 3600 * 0.9996)
    counter.boundary(datetime(2026, 7, 15, 13, 0, tzinfo=UTC))
    assert counter.today == 999


def test_the_polled_power_sensor_is_sample_and_hold_every_five_minutes() -> None:
    ac = climate(initial_mode="cool", initial_temp_c=28.0, power_sensor=True, power_poll_s=300.0)
    start = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)
    ac.advance(start, 30.0)
    history = []
    t = start
    for _ in range(40):
        t += timedelta(seconds=30)
        ac.advance(t, 30.0)
        state = next(s for s in ac.states(t) if s.entity_id == "sensor.sim_ac_power")
        history.append((t, float(state.state)))
    changes = [b[0] for a, b in itertools.pairwise(history) if a[1] != b[1]]
    assert changes
    assert all(c.timestamp() % 300 == 0 for c in changes)
    assert ac.cadences()["sensor.sim_ac_power"] == 300.0
    assert ac.roles()["power"] == "sensor.sim_ac_power"


# --- the LG integration ----------------------------------------------------------------


def call(device: SimClimate, service: str, **data: object) -> object:
    return device.call(
        T0, "climate", service, "climate.sim_ac", validate_schema("climate", service, data)
    )


def test_set_temperature_on_an_off_unit_turns_it_on_and_blocks_for_the_sleep() -> None:
    ac = climate(initial_mode="off", default_on_mode="cool")
    effect = call(ac, "set_temperature", temperature=23.0)
    assert effect.blocking_s == pytest.approx(2.0 + ac.p.call_s)  # type: ignore[attr-defined]
    assert [s.at_s for s in effect.steps] == [1.0, 3.0]  # type: ignore[attr-defined]
    for step in effect.steps:  # type: ignore[attr-defined]
        step.change()
    assert ac.mode == "cool"
    assert ac.setpoint_c == 23.0


def test_the_integration_truncates_with_int_when_the_step_is_one_or_more() -> None:
    ac = climate(target_temp_step=1.0)
    effect = call(ac, "set_temperature", temperature=22.9)
    for step in effect.steps:  # type: ignore[attr-defined]
        step.change()
    assert ac.setpoint_c == 22.0
    half = climate(target_temp_step=0.5)
    for step in call(half, "set_temperature", temperature=22.9).steps:  # type: ignore[attr-defined]
        step.change()
    assert half.setpoint_c == 22.9  # no snapping below a step of 1


def test_a_missing_target_temp_step_raises_unknown_error_in_the_bug_variant() -> None:
    ac = climate(target_temp_step=None, step_none_bug=True)
    with pytest.raises(ServiceError) as excinfo:
        call(ac, "set_temperature", temperature=22.0)
    assert excinfo.value.code == "unknown_error"
    quiet = climate(target_temp_step=None)
    assert call(quiet, "set_temperature", temperature=22.5).steps  # type: ignore[attr-defined]


def test_the_target_is_none_while_the_unit_reports_off_and_state_arrives_by_push() -> None:
    ac = climate(initial_mode="off")
    attrs = ac.states(T0)[0].attributes
    assert attrs["temperature"] is None
    assert ac.states(T0)[0].state == "off"
    for step in call(ac, "set_hvac_mode", hvac_mode="cool").steps:  # type: ignore[attr-defined]
        step.change()
    assert ac.mode == "cool"
    assert ac.states(T0)[0].state == "off"  # not reported yet
    ac.report()
    assert ac.states(T0)[0].state == "cool"
    assert ac.states(T0)[0].attributes["temperature"] == 21.0


def test_service_validation_errors_have_the_home_assistant_shapes() -> None:
    ac = climate(min_temp_c=18, max_temp_c=30)
    with pytest.raises(ServiceError) as excinfo:
        call(ac, "set_temperature", temperature=35.0)
    assert excinfo.value.code == "service_validation_error"
    assert excinfo.value.message == (
        "Validation error: Provided temperature 35.0 is not valid. Accepted range is 18.0 to 30.0."
    )
    with pytest.raises(ServiceError) as excinfo:
        call(climate(hvac_modes=("off", "cool")), "set_hvac_mode", hvac_mode="heat")
    assert excinfo.value.code == "service_validation_error"
    assert "HVAC mode heat is not valid. Valid HVAC modes are: off, cool." in excinfo.value.message
    with pytest.raises(ServiceError, match="range"):  # no TARGET_TEMPERATURE_RANGE feature
        ac.call(
            T0,
            "climate",
            "set_temperature",
            "climate.sim_ac",
            {"target_temp_low": 20.0, "target_temp_high": 24.0},
        )
    no_off = climate(supported_features=1)
    with pytest.raises(ServiceError, match=r"does not support action climate\.turn_off"):
        call(no_off, "turn_off")


def test_a_second_entity_view_of_the_same_unit() -> None:
    ac = climate(mirror_entity_id="climate.sim_ac_b", mirror_hvac_modes=("off", "cool"))
    assert ac.handles("climate.sim_ac_b")
    states = {s.entity_id: s for s in ac.states(T0)}
    assert states["climate.sim_ac_b"].attributes["hvac_modes"] == ["off", "cool"]
    assert states["climate.sim_ac_b"].state == states["climate.sim_ac"].state


def test_manual_remote_actions() -> None:
    ac = climate(initial_mode="cool")
    for step in ac.manual("set_temperature", 25.0).steps:
        step.change()
    assert ac.setpoint_c == 25.0
    for step in ac.manual("turn_off", None).steps:
        step.change()
    assert ac.mode == "off"
    for step in ac.manual("turn_on", None).steps:
        step.change()
    assert ac.mode == "cool"  # the last mode comes back
    with pytest.raises(ValueError, match="manual action"):
        ac.manual("select_option", "x")


# --- schemas ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("domain", "service", "data", "code", "text"),
    [
        ("light", "turn_on", {}, "not_found", "Service light.turn_on not found."),
        ("number", "set_value", {}, "invalid_format", "required key not provided @ data['value']"),
        ("number", "set_value", {"value": "x"}, "invalid_format", "expected float"),
        (
            "number",
            "set_value",
            {"value": 1, "step": 2},
            "invalid_format",
            "extra keys not allowed",
        ),
        ("select", "select_option", {"option": 3}, "invalid_format", "expected str"),
        ("climate", "set_hvac_mode", {"hvac_mode": "sauna"}, "invalid_format", "HVACMode"),
        ("climate", "set_temperature", {}, "invalid_format", "must contain at least one of"),
        (
            "climate",
            "set_temperature",
            {"temperature": 1, "target_temp_low": 1, "target_temp_high": 2},
            "invalid_format",
            "exclusion",
        ),
        ("climate", "set_temperature", {"target_temp_low": 1}, "invalid_format", "inclusion"),
        ("water_heater", "set_temperature", {}, "invalid_format", "'temperature'"),
    ],
)
def test_schema_errors(
    domain: str, service: str, data: dict[str, object], code: str, text: str
) -> None:
    with pytest.raises(ServiceError) as excinfo:
        validate_schema(domain, service, data)
    assert excinfo.value.code == code
    assert text in excinfo.value.message


def test_schema_coerces_numbers_like_voluptuous() -> None:
    assert validate_schema("number", "set_value", {"value": "12.5"}) == {"value": 12.5}
    assert validate_schema(
        "climate", "set_temperature", {"temperature": 21, "hvac_mode": "cool"}
    ) == {
        "temperature": 21.0,
        "hvac_mode": "cool",
    }


# --- switch and number loads -----------------------------------------------------------


def test_a_switch_load_draws_its_nominal_power_when_on() -> None:
    pump = SimSwitchLoad("pump", SwitchParams(nominal_w=750.0, power_sensor=True))
    assert pump.power_w() == 0.0
    effect = pump.call(T0, "switch", "turn_on", "switch.sim_pump", {})
    for step in effect.steps:
        step.change()
    assert pump.power_w() == 750.0
    assert pump.states(T0)[0].state == "off"  # reported on the push
    pump.report()
    states = {s.entity_id: s.state for s in pump.states(T0)}
    assert states == {"switch.sim_pump": "on", "sensor.sim_pump_power": "750.0"}
    with pytest.raises(ServiceError):
        pump.call(T0, "climate", "turn_on", "switch.sim_pump", {})
    toggled = pump.call(T0, "switch", "toggle", "switch.sim_pump", {})
    for step in toggled.steps:
        step.change()
    assert not pump.is_on()
    assert pump.heartbeats() == {"sensor.sim_pump_power": 30.0}


def test_an_input_boolean_load_answers_to_its_own_domain() -> None:
    boolean = SimSwitchLoad("flag", SwitchParams(entity_id="input_boolean.flag", nominal_w=100.0))
    for step in boolean.call(T0, "input_boolean", "turn_on", "input_boolean.flag", {}).steps:
        step.change()
    assert boolean.power_w() == 100.0
    with pytest.raises(ServiceError):
        boolean.call(T0, "switch", "turn_on", "input_boolean.flag", {})


def test_a_number_load_checks_the_range_but_not_the_step() -> None:
    rod = SimNumberLoad("rod", NumberParams(max=3000.0, step=100.0))
    effect = rod.call(T0, "number", "set_value", "number.sim_rod", {"value": 1234.0})
    for step in effect.steps:
        step.change()
    assert rod.power_w() == 1234.0  # not snapped: core does not enforce the step
    with pytest.raises(ServiceError) as excinfo:
        rod.call(T0, "number", "set_value", "number.sim_rod", {"value": 4000.0})
    assert excinfo.value.message == (
        "Validation error: Value 4000.0 for number.sim_rod is outside valid range 0.0 - 3000.0."
    )
    rod.report()
    attrs = rod.states(T0)[0].attributes
    assert (attrs["min"], attrs["max"], attrs["step"]) == (0.0, 3000.0, 100.0)


# --- water heater ----------------------------------------------------------------------


def heater(**kw: object) -> SimWaterHeater:
    return SimWaterHeater("boiler", WaterHeaterParams(**kw))  # type: ignore[arg-type]


def select(device: SimWaterHeater, option: str) -> None:
    effect = device.call(T0, "select", "select_option", device.entity, {"option": option})
    for step in effect.steps:
        step.change()


def test_the_tank_heats_with_the_level_and_loses_heat_when_off() -> None:
    boiler = heater(initial_temp_c=40.0, draw_w=(0.0,) * 24)
    select(boiler, "high")
    assert boiler.power_w() == 3000.0
    t = run(boiler, T0, 3600, 20.0)
    assert 51.0 < boiler.temp_c < 54.0  # 3 kW into 200 l: about 12.9 K/h minus losses
    select(boiler, "off")
    cooled = run(boiler, t, 3600, 20.0)
    assert cooled > t
    assert boiler.temp_c < 54.0
    assert boiler.power_w() == 0.0


def test_the_thermostat_cuts_the_elements_off_at_the_maximum_and_back_in_below_the_hysteresis() -> (
    None
):
    boiler = heater(
        initial_temp_c=50.0, max_temp_c=55.0, cutoff_hysteresis_k=3.0, draw_w=(600.0,) * 24
    )
    select(boiler, "high")
    t = run(boiler, T0, 1800, 20.0)
    assert boiler.cut_off
    assert boiler.power_w() == 0.0
    assert boiler.is_on()  # still commanded on: the select shows "high"
    assert boiler.temp_c <= 55.0 + 0.5
    seen_back = False
    for _ in range(600):
        t += timedelta(minutes=1)
        boiler.advance(t, 20.0)
        boiler.advance(t + timedelta(seconds=1), 20.0)
        if not boiler.cut_off:
            seen_back = True
            break
    assert seen_back  # a hot water draw eventually brings it below max - 3 K


def test_the_option_must_exist_and_relays_switch_one_by_one() -> None:
    boiler = heater()
    with pytest.raises(ServiceError) as excinfo:
        boiler.call(T0, "select", "select_option", boiler.entity, {"option": "medium"})
    assert excinfo.value.message == (
        "Validation error: Option medium is not valid for entity select.sim_boiler_level, "
        "valid options are: off, low, high."
    )
    relays = heater(variant="relays", probe=False)
    assert relays.roles() == {
        "relay_1": "switch.sim_boiler_relay_1",
        "relay_2": "switch.sim_boiler_relay_2",
        "relay_3": "switch.sim_boiler_relay_3",
    }
    for entity in relays.relay_ids[:2]:
        for step in relays.call(T0, "switch", "turn_on", entity, {}).steps:
            step.change()
    assert relays.power_w() == 4000.0
    assert not any("temperature" in e for e in relays.entity_ids())


def test_the_dedicated_water_heater_entity_follows_the_verified_services() -> None:
    boiler = heater(
        variant="entity", levels=(("off", 0.0), ("eco", 1000.0), ("performance", 3000.0))
    )
    assert boiler.entity == "water_heater.sim_boiler"

    def do(service: str, **data: object) -> None:
        clean = validate_schema("water_heater", service, data)
        for step in boiler.call(T0, "water_heater", service, boiler.entity, clean).steps:
            step.change()

    do("set_operation_mode", operation_mode="performance")
    assert boiler.power_w() == 3000.0
    do("turn_off")
    assert boiler.power_w() == 0.0
    do("turn_on")  # back to the last non-off mode
    assert boiler.power_w() == 3000.0
    do("set_temperature", temperature=50.0, operation_mode="eco")
    assert boiler.target_c == 50.0
    assert boiler.power_w() == 1000.0
    with pytest.raises(ServiceError, match="Operation mode turbo is not valid"):
        do("set_operation_mode", operation_mode="turbo")
    boiler.report()
    attrs = boiler.states(T0)[0].attributes
    assert attrs["operation_list"] == ["off", "eco", "performance"]
    assert attrs["temperature"] == 50.0


def test_a_device_set_routes_entities_and_rejects_duplicates() -> None:
    devices = build_device_set(
        [
            ("pump", SwitchParams()),
            ("rod", NumberParams()),
            ("ac", ClimateParams()),
            ("boiler", WaterHeaterParams()),
        ]
    )
    assert devices.of_entity("number.sim_rod") is devices.devices["rod"]
    assert devices.of_entity("sensor.sim_boiler_temperature") is devices.devices["boiler"]
    assert devices.of_entity("switch.nope") is None
    assert devices.total_power_w() == pytest.approx(5.0)  # the unit's standby
    with pytest.raises(ValueError, match="duplicate"):
        build_device_set([("pump", SwitchParams()), ("pump", SwitchParams())])
    with pytest.raises(ValueError, match="two simulated devices"):
        build_device_set(
            [("a", SwitchParams(entity_id="switch.x")), ("b", SwitchParams(entity_id="switch.x"))]
        )
    assert devices.cadences() == {
        "sensor.sim_ac_energy_today": 3600.0,
        "sensor.sim_ac_energy_yesterday": 86_400.0,
    }
    assert devices.states(T0)


def test_advance_needs_an_aware_time() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        climate().advance(datetime(2026, 7, 15, 12, 0), 10.0)  # noqa: DTZ001
