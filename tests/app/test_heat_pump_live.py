# SPDX-License-Identifier: Apache-2.0
"""Live COP and thermal-model quality in the control state (PR 7-I)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from suncore.app.runtime import Runtime
from suncore.app.thermal_live import heat_pump_live, outdoor_now
from suncore.core.controlstate import ControlState
from suncore.core.decisions import OperatingMode
from suncore.core.devices import ThermalSpec
from suncore.core.series import SeriesUnit, TimeSeries
from suncore.core.thermal import CopCurve, CopPoint, LearnedThermal, ThermalModel
from suncore.sim.planner_flex import heat_pump
from tests.api.conftest import make_control_state, make_device_state

NOW = datetime(2026, 11, 25, 10, 30, tzinfo=UTC)
FIT = LearnedThermal(
    model=ThermalModel(capacity_wh_per_k=10_000.0, loss_w_per_k=180.0),
    nrmse=0.1,
    history_days=30,
    fitted_at=NOW - timedelta(hours=6),
)
VENDOR = CopCurve(points=(CopPoint(outdoor_c=-10, cop=2.0), CopPoint(outdoor_c=10, cop=4.0)))


def _hourly(*values: float | None) -> TimeSeries:
    start = datetime(2026, 11, 25, 9, tzinfo=UTC)
    return TimeSeries(
        start=start, step=timedelta(hours=1), values=tuple(values), unit=SeriesUnit.CELSIUS
    )


def test_outdoor_now_is_the_hour_that_contains_now() -> None:
    series = _hourly(1.0, 4.0, 6.0)
    assert outdoor_now(series, NOW) == 4.0
    assert outdoor_now(series, NOW + timedelta(hours=2)) is None  # past the last hour
    assert outdoor_now(series, NOW - timedelta(hours=3)) is None
    assert outdoor_now(None, NOW) is None
    assert outdoor_now(_hourly(None), datetime(2026, 11, 25, 9, 10, tzinfo=UTC)) is None


def test_the_generic_curve_is_labelled_assumed() -> None:
    device = heat_pump()
    live = heat_pump_live(device, {}, 2.0)
    assert live is not None
    assert live.cop == 3.0  # GENERIC_AIR_WATER_COP at 2 degC
    assert live.cop_assumed is True
    assert live.outdoor_c == 2.0


def test_a_curve_between_points_and_clamped_at_the_ends() -> None:
    device = heat_pump()
    assert heat_pump_live(device, {}, -7.0).cop == 2.5  # type: ignore[union-attr]
    assert heat_pump_live(device, {}, -30.0).cop == 2.5  # type: ignore[union-attr]
    assert heat_pump_live(device, {}, 4.5).cop == 3.3  # type: ignore[union-attr]


def test_a_vendor_curve_is_not_assumed() -> None:
    thermal = ThermalSpec(cop=VENDOR, learned=FIT)
    device = heat_pump().model_copy(update={"thermal": thermal})
    live = heat_pump_live(device, {}, 0.0)
    assert live is not None
    assert (live.cop, live.cop_assumed) == (3.0, False)


def test_a_curve_the_user_marked_assumed_stays_assumed() -> None:
    curve = VENDOR.model_copy(update={"assumed": True})
    device = heat_pump().model_copy(update={"thermal": ThermalSpec(cop=curve)})
    live = heat_pump_live(device, {}, 0.0)
    assert live is not None
    assert live.cop_assumed is True


def test_without_an_outdoor_temperature_the_cop_is_unknown_not_zero() -> None:
    live = heat_pump_live(heat_pump(), {}, None)
    assert live is not None
    assert (live.outdoor_c, live.cop) == (None, None)
    assert live.cop_assumed is True


def test_model_quality_and_whether_the_planner_uses_it() -> None:
    device = heat_pump()
    live = heat_pump_live(device, {device.id: FIT}, 5.0)
    assert live is not None
    assert live.model is not None
    assert live.model.fit_error == 0.1
    assert live.model.history_days == 30
    assert live.model.used_by_planner is True
    poor = FIT.model_copy(update={"nrmse": 0.45})
    short = FIT.model_copy(update={"history_days": 5})
    for fit in (poor, short):
        live = heat_pump_live(device, {device.id: fit}, 5.0)
        assert live is not None
        assert live.model is not None
        assert live.model.used_by_planner is False


def test_the_learner_fit_wins_over_the_stored_one_and_none_means_no_model() -> None:
    device = heat_pump()  # carries a stored fit with nrmse 0.1
    fresh = FIT.model_copy(update={"nrmse": 0.15})
    live = heat_pump_live(device, {device.id: fresh}, 5.0)
    assert live is not None
    assert live.model is not None
    assert live.model.fit_error == 0.15
    bare = device.model_copy(update={"thermal": ThermalSpec()})
    live = heat_pump_live(bare, {}, 5.0)
    assert live is not None
    assert live.model is None


def test_a_device_without_a_thermal_section_has_no_live_values() -> None:
    plain = heat_pump().model_copy(update={"thermal": None})
    assert heat_pump_live(plain, {}, 5.0) is None


def _runtime(*, outdoor: TimeSeries | None, models: dict[str, LearnedThermal]) -> Any:
    return SimpleNamespace(
        devices=SimpleNamespace(
            devices=[heat_pump("hp"), heat_pump("plain").model_copy(update={"thermal": None})]
        ),
        clock=lambda: NOW,
        load_service=None
        if outdoor is None
        else SimpleNamespace(outdoor_forecast=lambda _now: outdoor),
        thermal=SimpleNamespace(models=models),
    )


def _state() -> ControlState:
    return make_control_state(devices=[make_device_state("hp"), make_device_state("plain")])


def test_the_runtime_adds_the_live_values_to_thermal_devices_only() -> None:
    rt = _runtime(outdoor=_hourly(1.0, 4.0, 6.0), models={})
    state = Runtime._with_heat_pumps(rt, _state())
    by_id = {d.device_id: d for d in state.devices}
    assert by_id["plain"].heat_pump is None
    live = by_id["hp"].heat_pump
    assert live is not None
    assert (live.outdoor_c, live.cop, live.cop_assumed) == (4.0, 3.24, True)
    assert live.model is not None
    assert live.model.used_by_planner is True
    # everything else about the device is the stack's state, unchanged
    assert by_id["hp"].global_mode is OperatingMode.SHADOW


def test_the_runtime_without_weather_still_reports_the_model() -> None:
    rt = _runtime(outdoor=None, models={})
    state = Runtime._with_heat_pumps(rt, _state())
    live = {d.device_id: d for d in state.devices}["hp"].heat_pump
    assert live is not None
    assert live.cop is None
    assert live.model is not None


def test_without_thermal_devices_the_state_is_returned_as_it_is() -> None:
    rt = SimpleNamespace(devices=None, clock=lambda: NOW, load_service=None, thermal=None)
    state = _state()
    assert Runtime._with_heat_pumps(rt, state) is state
