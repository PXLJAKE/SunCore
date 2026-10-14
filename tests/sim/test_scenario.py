# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import pytest
from pydantic import ValidationError

from suncore.sim.scenario import Scenario, list_scenarios, load_scenario, parse_scenario

pytestmark = pytest.mark.simulator

BUNDLED = [
    "climate_counter_only",
    "climate_measured_slow",
    "climate_min_run",
    "cloud_flicker",
    "grid_limit",
    "ha_disconnect",
    "inverter_hang",
    "manual_override",
    "sensor_dropout",
    "summer_day",
    "water_heater_steps",
    "winter_fog",
]


def test_bundled_scenarios_listed() -> None:
    assert list_scenarios() == BUNDLED


@pytest.mark.parametrize("name", BUNDLED)
def test_scenario_runs_to_completion(name: str) -> None:
    sc = load_scenario(name)
    assert sc.start.tzinfo is not None
    states = list(sc.build_house().run(sc.steps))
    assert len(states) == sc.steps
    b = sc.house.battery
    for s in states:
        assert s.pv_w + s.grid_w == pytest.approx(s.load_w + s.battery_w, abs=1e-6)
        assert b.soc_min_pct - 1e-9 <= s.soc_pct <= b.soc_max_pct + 1e-9
        assert 0 <= s.pv_w <= sc.house.pv.kwp * 1000


@pytest.mark.parametrize("name", BUNDLED)
def test_scenario_is_deterministic(name: str) -> None:
    sc = load_scenario(name)
    assert list(sc.build_house().run(sc.steps)) == list(sc.build_house().run(sc.steps))


def test_summer_beats_foggy_winter() -> None:
    def pv_wh(name: str) -> float:
        sc = load_scenario(name)
        return sum(s.pv_w for s in sc.build_house().run(sc.steps)) * sc.step_s / 3600

    assert pv_wh("summer_day") > 5 * pv_wh("winter_fog")


def test_sensor_dropout_faults() -> None:
    sc = load_scenario("sensor_dropout")
    assert {f.kind for f in sc.faults} == {"unavailable", "stale", "disconnect"}
    assert [f.kind for f in sc.active_faults(7200)] == ["unavailable"]
    assert sc.active_faults(9000) == []
    assert [f.kind for f in sc.active_faults(28800)] == ["disconnect"]


def test_cloud_series_piecewise() -> None:
    sc = load_scenario("summer_day")
    series = sc.cloud_series()
    assert len(series) == sc.steps
    assert series[0] == 1.0
    assert series[21600 // 60] == 0.8
    assert series[-1] == 1.0


def test_unknown_and_invalid_names() -> None:
    with pytest.raises(FileNotFoundError):
        load_scenario("nope")
    with pytest.raises(FileNotFoundError):
        load_scenario("../house")


_BASE = 'name: x\nstart: "2026-06-21T00:00:00+02:00"\nduration_s: 600\nstep_s: 60\n'


def test_parse_minimal_and_rejections() -> None:
    assert isinstance(parse_scenario(_BASE), Scenario)
    bad = [
        _BASE.replace("+02:00", ""),  # naive start
        _BASE + "bogus: 1\n",
        _BASE.replace("600", "30"),  # shorter than a step
        _BASE + "faults: [{kind: unavailable, from_s: 0, to_s: 10}]\n",  # no target
        _BASE + "faults: [{kind: disconnect, from_s: 10, to_s: 10}]\n",
        _BASE + "clouds: [{at_s: 60, factor: 0.5}, {at_s: 0, factor: 0.5}]\n",
        _BASE + "clouds: [{at_s: 0, factor: 2}]\n",
    ]
    for text in bad:
        with pytest.raises(ValidationError):
            parse_scenario(text)


_PUMP = "devices: [{id: pump, physics: {kind: switch}}]\n"


def test_devices_manual_events_and_faults_are_validated() -> None:
    ok = parse_scenario(
        _BASE
        + _PUMP
        + "manual: [{at_s: 5, device: pump, action: turn_off}]\n"
        + 'faults: [{kind: unavailable, target: "device:pump", from_s: 0, to_s: 10},'
        + " {kind: frozen, target: sensor.x_y, from_s: 0, to_s: 10},"
        + " {kind: stale, target: pv_power, from_s: 0, to_s: 10}]\n"
    )
    assert ok.manual[0].source == "remote"
    assert [f.kind for f in ok.active_faults(5)] == ["unavailable", "frozen", "stale"]
    bad = [
        _BASE
        + _PUMP
        + _PUMP.replace("devices: [", "devices: [{id: pump, physics: {kind: number}},"),
        _BASE + _PUMP + "manual: [{at_s: 5, device: ghost, action: turn_off}]\n",
        _BASE + _PUMP + "manual: [{at_s: 5, device: pump, action: explode}]\n",
        _BASE + _PUMP + 'faults: [{kind: stale, target: "device:ghost", from_s: 0, to_s: 5}]\n',
        _BASE + 'faults: [{kind: stale, target: "not an entity", from_s: 0, to_s: 5}]\n',
        _BASE + "devices: [{id: pump, physics: {kind: switch}, control: {priority: 1}}]\n",
        _BASE + "devices: [{id: Pump, physics: {kind: switch}}]\n",
        _BASE + "outdoor: {amplitude_c: -1}\n",
    ]
    for text in bad:
        with pytest.raises(ValidationError):
            parse_scenario(text)


def test_outdoor_temperature_is_a_daily_cosine() -> None:
    from datetime import UTC, datetime  # noqa: PLC0415

    sc = parse_scenario(_BASE + "outdoor: {mean_c: 20, amplitude_c: 6, peak_hour: 15}\n")
    assert sc.outdoor.at(datetime(2026, 6, 21, 15, 0, tzinfo=UTC)) == pytest.approx(26.0)
    assert sc.outdoor.at(datetime(2026, 6, 21, 3, 0, tzinfo=UTC)) == pytest.approx(14.0)


@pytest.mark.parametrize(
    "name",
    [n for n in BUNDLED if n not in ("summer_day", "winter_fog")],
)
def test_closed_loop_scenarios_declare_devices(name: str) -> None:
    sc = load_scenario(name)
    controlled = [d for d in sc.devices if d.template is not None]
    assert controlled, "a closed-loop scenario controls at least one device"
    assert sc.step_s <= 60  # the controller tick (ControlSettings.tick_s <= 60)
