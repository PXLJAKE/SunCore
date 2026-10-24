# SPDX-License-Identifier: Apache-2.0
"""PlannerInputs: the pure builder from the services' outputs (Phase 4 WP8)."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, time, timedelta

import pytest

from suncore.core.battery import (
    BatteryModel,
    BatterySettings,
    BatteryStrategy,
    GridChargePolicy,
)
from suncore.core.config import BatteryTarget, GoalsConfig
from suncore.core.decisions import Reason
from suncore.core.devices import (
    ContinuousControl,
    DeviceConfig,
    DeviceKind,
    OnOffControl,
    PowerModel,
    PowerModelKind,
    Step,
    StepsControl,
    TimeWindow,
)
from suncore.core.site import BatteryConfig, EntityRef
from suncore.core.tariff import WeeklySchedule
from suncore.planner import reasons as rk
from suncore.planner.inputs import (
    DEFAULT_BASE_LOAD_W,
    PlannerBattery,
    PlannerInputs,
    PriceMode,
    SocGoal,
    buffer_goal,
    build_inputs,
    device_max_power_w,
    grid_charge_allowance,
    window_allows,
)
from suncore.sim.planner_scenarios import (
    SUMMER,
    TZ,
    climate,
    forecasts,
    prices,
    situation,
)

NOW = datetime(SUMMER.year, SUMMER.month, SUMMER.day, 6, 7, tzinfo=TZ)
SETTINGS = BatterySettings(
    model=BatteryModel(capacity_wh=10_000, max_charge_w=5_000, max_discharge_w=5_000)
)


@pytest.fixture(scope="module")
def series():  # type: ignore[no-untyped-def]
    starts, pv, load = forecasts(replace(situation("sunny_day"), now=NOW))
    return starts, pv, load


def _build(series, **kw):  # type: ignore[no-untyped-def]
    starts, pv, load = series
    args = {
        "tz": TZ,
        "load": load,
        "pv": pv,
        "prices": prices(starts, [0.3] * len(starts), [0.08] * len(starts)),
        "battery": SETTINGS,
        "soc_pct": 40.0,
    }
    args.update(kw)
    return build_inputs(NOW, **args)


def test_the_grid_starts_at_the_quarter_hour_and_covers_48_hours(series) -> None:  # type: ignore[no-untyped-def]
    inputs = _build(series)
    assert inputs.starts[0] == datetime(2026, 6, 20, 4, 0, tzinfo=UTC)
    assert inputs.end >= NOW + timedelta(hours=48)
    assert inputs.n == 193
    assert inputs.price_mode is PriceMode.MARKET
    assert inputs.assumptions["price_mode"] == "market"
    assert inputs.reasons == ()


def test_missing_prices_are_filled_with_the_median_and_reported(series) -> None:  # type: ignore[no-untyped-def]
    starts = series[0]
    half = len(starts) // 2
    values = [0.2 if i % 2 else 0.4 for i in range(half)] + [None] * (len(starts) - half)
    inputs = _build(series, prices=prices(starts, values, [0.08] * len(starts)))
    assert inputs.price_mode is PriceMode.MARKET
    assert inputs.import_price[-1] == pytest.approx(0.3)
    assert inputs.import_known[-1] is None
    assert inputs.reasons[0].key == rk.INPUT_PRICES_PARTIAL
    assert inputs.reasons[0].args["filled_slots"] == inputs.assumptions["prices_filled_slots"]
    assert inputs.assumptions["prices_filled_slots"] > 0


def test_spot_only_prices_are_refused(series) -> None:  # type: ignore[no-untyped-def]
    starts = series[0]
    spot = prices(starts, [0.1] * len(starts), [0.08] * len(starts), includes="spot_only")
    inputs = _build(series, prices=spot)
    assert inputs.price_mode is PriceMode.FLAT
    assert set(inputs.import_price) == {1.0}
    assert inputs.reasons[0] == Reason(
        key=rk.FALLBACK_SPOT_WITHOUT_FORMULA, args={"source": "sim.tibber"}
    )


def test_unknown_prices_everywhere_mean_flat_mode(series) -> None:  # type: ignore[no-untyped-def]
    starts = series[0]
    inputs = _build(series, prices=prices(starts, [None] * len(starts), [None] * len(starts)))
    assert inputs.price_mode is PriceMode.FLAT
    assert inputs.reasons[0].key == rk.FALLBACK_NO_PRICES
    assert set(inputs.export_price) == {0.0}


def test_missing_forecasts_are_explicit(series) -> None:  # type: ignore[no-untyped-def]
    inputs = _build(series, pv=None, load=None, prices=None)
    keys = {r.key for r in inputs.reasons}
    assert keys == {rk.FALLBACK_NO_PRICES, rk.INPUT_NO_PV_FORECAST, rk.INPUT_NO_LOAD_FORECAST}
    assert set(inputs.pv_p50) == {0.0}


def test_without_soc_or_model_the_battery_is_left_out(series) -> None:  # type: ignore[no-untyped-def]
    inputs = _build(series, soc_pct=None)
    assert inputs.battery is None
    assert rk.INPUT_NO_BATTERY_SOC in {r.key for r in inputs.reasons}
    assert _build(series, battery=BatterySettings()).battery is None


def test_the_site_battery_narrows_the_model(series) -> None:  # type: ignore[no-untyped-def]
    site = BatteryConfig(
        power=EntityRef(entity_id="sensor.bat_power"),
        soc=EntityRef(entity_id="sensor.bat_soc"),
        min_soc_pct=25.0,
        max_soc_pct=90.0,
        max_charge_w=3_000,
    )
    battery = _build(series, site_battery=site).battery
    assert battery is not None
    assert battery.reserve_pct == 25.0
    assert battery.max_soc_pct == 90.0
    assert battery.max_charge_w == 3_000
    assert battery.max_discharge_w == 5_000


def test_the_user_target_is_a_timed_goal_on_every_day(series) -> None:  # type: ignore[no-untyped-def]
    goals = GoalsConfig(battery_target=BatteryTarget(soc_pct=80, time="18:00"))
    inputs = _build(series, goals=goals)
    user = [g for g in inputs.goals if g.kind == "user_target"]
    assert [g.at.astimezone(TZ).time() for g in user] == [time(18, 0), time(18, 0)]
    assert {g.at.astimezone(TZ).day for g in user} == {20, 21}
    assert all(g.reason.key == rk.USER_TARGET for g in user)


def test_risk_targets_become_goals(series) -> None:  # type: ignore[no-untyped-def]
    @dataclass_like
    class Target:
        soc_pct: float
        reason: Reason

    evening = Target(60.0, Reason(key=rk.EVENING_TARGET, args={"soc_pct": 60.0}))
    buffer = Target(50.0, Reason(key=rk.MORNING_BUFFER, args={"soc_pct": 50.0}))
    at = datetime(2026, 6, 20, 21, 0, tzinfo=TZ)
    inputs = _build(series, evening_target=evening, morning_buffer=buffer, evening_at=at)
    kinds = {g.kind: g for g in inputs.goals}
    assert kinds["evening_target"].at == at
    assert kinds["evening_target"].soc_pct == 60.0
    assert kinds["morning_buffer"].soc_pct == 50.0
    assert kinds["morning_buffer"].at < at
    assert inputs.morning_buffer_pct == 50.0
    # An evening that has passed is no goal.
    late = _build(series, evening_target=evening, evening_at=NOW - timedelta(hours=1))
    assert not [g for g in late.goals if g.kind == "evening_target"]


def dataclass_like(cls):  # type: ignore[no-untyped-def]
    from dataclasses import dataclass  # noqa: PLC0415

    return dataclass(frozen=True)(cls)


def test_prefer_full_without_a_buffer_goes_battery_first_to_max(series) -> None:  # type: ignore[no-untyped-def]
    settings = SETTINGS.model_copy(update={"strategy": BatteryStrategy.PREFER_FULL})
    inputs = _build(series, battery=settings)
    goal = next(g for g in inputs.goals if g.kind == "prefer_full")
    assert goal.soc_pct == 100.0
    assert goal.reason.key == rk.PREFER_FULL


def test_the_buffer_goal_is_the_reachable_soc() -> None:
    model = BatteryModel(capacity_wh=10_000, max_charge_w=5_000, max_discharge_w=5_000)
    battery = PlannerBattery(model, 20.0, 15.0, 100.0, 5_000, 5_000)
    start = datetime(2026, 6, 20, 6, 0, tzinfo=UTC)
    starts = [start + timedelta(minutes=15 * i) for i in range(8)]
    step = timedelta(minutes=15)
    reach = buffer_goal(
        battery,
        30.0,
        starts=starts,
        step=step,
        pv=[2_000.0] * 8,
        load=[0.0] * 8,
        until=starts[-1] + step,
    )
    assert reach is not None
    assert reach[1] == 30.0
    # 2 kW * 0.25 h * 0.95 = 475 Wh per slot: 1000 Wh need three slots.
    assert reach[0] == starts[2] + step
    weak = buffer_goal(
        battery,
        90.0,
        starts=starts,
        step=step,
        pv=[1_000.0] * 8,
        load=[0.0] * 8,
        until=starts[-1] + step,
    )
    assert weak is not None
    assert weak[0] == starts[-1] + step
    assert weak[1] == pytest.approx(20 + 8 * 2.375)
    assert (
        buffer_goal(
            battery, 10.0, starts=starts, step=step, pv=[0.0] * 8, load=[0.0] * 8, until=starts[-1]
        )
        is None
    )
    assert (
        buffer_goal(
            battery,
            50.0,
            starts=starts,
            step=step,
            pv=[0.0] * 8,
            load=[500.0] * 8,
            until=starts[-1],
        )
        is None
    )


def test_grid_charge_allowance_follows_policy_spread_and_import_limit() -> None:
    model = BatteryModel(capacity_wh=10_000, max_charge_w=5_000, max_discharge_w=5_000)
    battery = PlannerBattery(model, 20.0, 15.0, 100.0, 5_000, 5_000)
    start = datetime(2026, 12, 8, 0, 0, tzinfo=TZ)
    starts = [start + timedelta(hours=i) for i in range(8)]
    known: list[float | None] = [0.15, 0.15, None, 0.15, 0.35, 0.35, 0.18, 0.35]
    policy = GridChargePolicy(
        enabled=True, max_price=0.2, schedule=WeeklySchedule.daily("00:00", "06:00")
    )
    allowed = grid_charge_allowance(
        starts,
        known,
        load_w=[300.0] * 8,
        battery=battery,
        policy=policy,
        tz=TZ,
        import_limit_w=4_000,
    )
    assert allowed == [3_700.0, 3_700.0, 0.0, 3_700.0, 0.0, 0.0, 0.0, 0.0]
    off = grid_charge_allowance(
        starts,
        known,
        load_w=[300.0] * 8,
        battery=battery,
        policy=GridChargePolicy(),
        tz=TZ,
        import_limit_w=None,
    )
    assert off == [0.0] * 8
    # A flat tariff has no spread rule: the policy alone decides.
    flat = grid_charge_allowance(
        starts,
        [0.3] * 8,
        load_w=[0.0] * 8,
        battery=battery,
        policy=GridChargePolicy(enabled=True, schedule=WeeklySchedule.daily("00:00", "02:00")),
        tz=TZ,
        import_limit_w=None,
    )
    assert flat == [5_000.0, 5_000.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]


def _device(control, nominal=None, windows=()):  # type: ignore[no-untyped-def]
    return DeviceConfig(
        id="d",
        template_id="generic.x",
        name="d",
        kind=next(iter(DeviceKind)),
        entities={"sw": EntityRef(entity_id="number.d")},
        power_model=PowerModel(kind=PowerModelKind.NOMINAL, nominal_w=nominal)
        if nominal
        else PowerModel(kind=PowerModelKind.MEASURED, power_role="sw"),
        control=control,
        windows=windows,
    )


def test_device_max_power_from_the_control() -> None:
    steps = StepsControl(
        kind="steps", role="sw", steps=(Step(value=0, power_w=0), Step(value=1, power_w=900))
    )
    assert device_max_power_w(_device(steps)) == 900
    amps = ContinuousControl(kind="continuous", role="sw", unit="A", min=6, max=16, phases=3)
    assert device_max_power_w(_device(amps)) == pytest.approx(16 * 230 * 3)
    watts = ContinuousControl(kind="continuous", role="sw", unit="W", min=100, max=2000)
    assert device_max_power_w(_device(watts)) == 2000
    pct = ContinuousControl(kind="continuous", role="sw", unit="%", min=0, max=100)
    assert device_max_power_w(_device(pct, nominal=1800.0)) == 1800.0
    assert device_max_power_w(_device(OnOffControl(kind="on_off", role="sw"))) is None
    assert device_max_power_w(climate("ac", 700.0)) == 700.0


def test_devices_with_unknown_power_are_left_out_with_a_reason(series) -> None:  # type: ignore[no-untyped-def]
    switch = _device(OnOffControl(kind="on_off", role="sw"))
    inputs = _build(series, devices=[switch])
    assert inputs.devices == ()
    assert inputs.reasons[-1].key == rk.INPUT_DEVICE_POWER_UNKNOWN
    known = _build(series, devices=[switch], device_max_w={"d": 1500.0})
    assert known.devices[0].max_w == 1500.0


def test_device_windows_block_slots(series) -> None:  # type: ignore[no-untyped-def]
    window = TimeWindow(start=time(10, 0), end=time(14, 0))
    quiet = TimeWindow(start=time(12, 0), end=time(13, 0), mode="block")
    device = climate("ac", 800.0).model_copy(update={"windows": (window, quiet)})
    inputs = _build(series, devices=[device])
    allowed = {
        s.astimezone(TZ).hour
        for s, ok in zip(inputs.starts, inputs.devices[0].allowed, strict=True)
        if ok
    }
    assert allowed == {10, 11, 13}
    assert window_allows((), NOW)


def test_overnight_windows() -> None:
    night = TimeWindow(start=time(22, 0), end=time(6, 0), days=(0,))
    monday_late = datetime(2026, 10, 19, 23, 0, tzinfo=TZ)
    tuesday_early = datetime(2026, 10, 20, 5, 0, tzinfo=TZ)
    tuesday_late = datetime(2026, 10, 20, 23, 0, tzinfo=TZ)
    assert window_allows((night,), monday_late)
    assert window_allows((night,), tuesday_early)
    assert not window_allows((night,), tuesday_late)


def test_inputs_are_validated() -> None:
    start = datetime(2026, 6, 20, 4, 0, tzinfo=UTC)
    base = {
        "now": start,
        "starts": (start,),
        "step": timedelta(minutes=15),
        "tz": TZ,
        "import_price": (0.3,),
        "export_price": (0.08,),
        "import_known": (0.3,),
        "price_mode": PriceMode.MARKET,
        "pv_p10": (0.0,),
        "pv_p50": (0.0,),
        "pv_p90": (0.0,),
        "load_p10": (0.0,),
        "load_p50": (0.0,),
        "load_p90": (0.0,),
    }
    inputs = PlannerInputs(**base)  # type: ignore[arg-type]
    assert inputs.slot_of(start) is None
    assert inputs.slot_of(start + timedelta(minutes=10)) == 0
    assert inputs.slot_of(start + timedelta(hours=1)) is None
    assert inputs.grid_charge_at(0) == 0.0
    with pytest.raises(ValueError, match="one value per slot"):
        PlannerInputs(**{**base, "pv_p50": (0.0, 1.0)})  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="finite"):
        PlannerInputs(**{**base, "pv_p50": (float("nan"),)})  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="empty"):
        PlannerInputs(**{**base, "starts": ()})  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="grid_charge_w"):
        PlannerInputs(**{**base, "grid_charge_w": (1.0, 2.0)})  # type: ignore[arg-type]


def test_no_grid_charging_without_a_known_price(series) -> None:  # type: ignore[no-untyped-def]
    """Review D2: flat mode or an unknown price never allows grid charging, not even under a
    schedule-only policy."""
    night = GridChargePolicy(enabled=True, schedule=WeeklySchedule.daily("00:00", "23:59"))
    settings = SETTINGS.model_copy(update={"grid_charge": night})
    assert not any(_build(series, battery=settings, prices=None).grid_charge_w)
    starts = series[0]
    spot = prices(starts, [0.1] * len(starts), [0.08] * len(starts), includes="spot_only")
    assert not any(_build(series, battery=settings, prices=spot).grid_charge_w)
    half = [0.3] * 60 + [None] * (len(starts) - 60)
    partial = _build(series, battery=settings, prices=prices(starts, half, [0.08] * len(starts)))
    assert all(
        w == 0
        for w, k in zip(partial.grid_charge_w, partial.import_known, strict=True)
        if k is None
    )
    assert any(w > 0 for w in partial.grid_charge_w)


def test_negative_rule_slots_discourage_export(series) -> None:  # type: ignore[no-untyped-def]
    starts = series[0]
    imp = [-0.05 if 30 <= i < 40 else 0.3 for i in range(len(starts))]
    exp = [0.0 if 30 <= i < 40 else 0.08 for i in range(len(starts))]
    inputs = _build(series, prices=prices(starts, imp, exp))
    assert [inputs.discouraged(t) for t in range(28, 42)] == [False] * 2 + [True] * 10 + [False] * 2
    # Export "none" (price 0 everywhere) with positive import prices is not discouraged.
    plain = _build(series, prices=prices(starts, [0.3] * len(starts), [0.0] * len(starts)))
    assert not any(plain.export_discouraged)


def test_without_a_load_forecast_a_flat_load_and_no_devices(series) -> None:  # type: ignore[no-untyped-def]
    inputs = _build(series, load=None, devices=[climate("ac", 800.0)])
    assert set(inputs.load_p50) == {DEFAULT_BASE_LOAD_W}
    assert inputs.devices == ()
    reason = next(r for r in inputs.reasons if r.key == rk.INPUT_NO_LOAD_FORECAST)
    assert reason.args["load_w"] == DEFAULT_BASE_LOAD_W
    recent = _build(series, load=None, load_fallback_w=730.0)
    assert set(recent.load_p50) == {730.0}


@dataclass(frozen=True)
class _Target:
    soc_pct: float
    reason: Reason


def test_a_full_evening_target_is_due_when_the_evening_deficit_starts(series) -> None:  # type: ignore[no-untyped-def]
    """Review m2: at 100 % the target sits before the evening deficit, so a full battery is
    never held while the house imports."""
    evening = datetime(2026, 6, 20, 21, 15, tzinfo=TZ)
    full = _Target(100.0, Reason(key=rk.EVENING_TARGET, args={"soc_pct": 100.0}))
    inputs = _build(series, evening_target=full, evening_at=evening)
    goal = next(g for g in inputs.goals if g.kind == "evening_target")
    assert goal.at < evening
    slot = inputs.slot_of(goal.at)
    assert slot is not None
    assert inputs.load_p50[slot + 1] >= inputs.pv_p50[slot + 1]
    assert inputs.load_p50[slot] < inputs.pv_p50[slot]
    half = _Target(60.0, Reason(key=rk.EVENING_TARGET, args={"soc_pct": 60.0}))
    plain = _build(series, evening_target=half, evening_at=evening)
    assert next(g for g in plain.goals if g.kind == "evening_target").at == evening


def test_an_unreachable_buffer_names_the_reachable_soc(series) -> None:  # type: ignore[no-untyped-def]
    pv = series[1]
    scaled = tuple(None if v is None else v * 0.2 for v in pv.p50.values)
    weak = type(pv).deterministic(pv.p50.model_copy(update={"values": scaled}))
    buffer = _Target(90.0, Reason(key=rk.MORNING_BUFFER, args={"soc_pct": 90.0}))
    inputs = _build(series, pv=weak, morning_buffer=buffer, soc_pct=20.0)
    goal = next(g for g in inputs.goals if g.kind == "morning_buffer")
    assert goal.soc_pct < 90.0
    assert goal.reason.args["soc_pct"] == pytest.approx(round(goal.soc_pct, 1))


def test_soc_goal_is_plain_data() -> None:
    goal = SocGoal("user_target", 50.0, NOW, Reason(key=rk.USER_TARGET))
    assert goal.kind == "user_target"
