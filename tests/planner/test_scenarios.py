# SPDX-License-Identifier: Apache-2.0
"""Planner scenarios of the virtual house (Phase 4 WP8): what each situation must produce."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

from suncore.core.battery import BatteryStrategy, RiskLevel
from suncore.core.control import BandPreference
from suncore.planner import reasons as rk
from suncore.planner.heuristic import heuristic_schedule
from suncore.planner.inputs import PriceMode
from suncore.planner.model import economics, goal_shortfall_wh, violations
from suncore.planner.run import PlannerRun, run
from suncore.sim.planner_scenarios import (
    SCENARIOS,
    TZ,
    scenario_inputs,
    situation,
    situation_inputs,
)
from tests.planner.replay import battery_first_soc, replay

EPS = 1.0


def _keys(result: PlannerRun, t: int) -> set[str]:
    return {r.key for r in result.plan.intervals[t].reasons}


def _hour(result: PlannerRun, t: int) -> float:
    local = result.plan.intervals[t].start.astimezone(TZ)
    return local.hour + local.minute / 60


@pytest.mark.parametrize("name", SCENARIOS)
def test_every_scenario_is_solved_feasibly_and_beats_the_heuristic(name: str) -> None:
    inputs = scenario_inputs(name)
    result = run(inputs)
    assert result.source == "solver"
    assert result.plan.stats is not None
    assert result.plan.stats.status == "optimal"
    assert result.plan.stats.n_binaries == 0
    assert violations(inputs, result.schedule) == []
    assert violations(inputs, heuristic_schedule(inputs)) == []
    assert result.objective <= result.heuristic_objective + 1e-9
    assert all(interval.reasons for interval in result.plan.intervals)
    assert result.plan.reasons[0].key == rk.SOLVER_OPTIMAL


def test_sunny_day_meets_the_evening_target_and_runs_devices_on_surplus() -> None:
    inputs = scenario_inputs("sunny_day")
    result = run(inputs)
    econ = economics(inputs)
    assert [g.kind for g in inputs.goals] == ["evening_target"]
    assert goal_shortfall_wh(inputs, econ, result.schedule) == [0.0]
    device_wh = sum(sum(d) for d in result.schedule.devices_w) * inputs.dt_h
    assert device_wh > 5_000
    assert not any(result.schedule.grid_charge_w)
    assert any(rk.DEVICE_SURPLUS in _keys(result, t) for t in range(inputs.n))
    # The evening target's own reason is shown at its slot.
    slot = econ.goal_slots[0]
    assert slot is not None
    assert rk.EVENING_TARGET in _keys(result, slot)


def test_mixed_clouds_keeps_the_target() -> None:
    inputs = scenario_inputs("mixed_clouds")
    result = run(inputs)
    assert goal_shortfall_wh(inputs, economics(inputs), result.schedule) == [0.0]


def test_two_poor_days_raise_the_evening_target_to_max_and_hold_the_battery() -> None:
    inputs = scenario_inputs("two_poor_days")
    result = run(inputs)
    evening = next(g for g in inputs.goals if g.kind == "evening_target")
    assert inputs.battery is not None
    assert evening.soc_pct == pytest.approx(inputs.battery.max_soc_pct)
    assert evening.reason.args["cause"] in ("next_day_deficit", "max_soc")
    econ = economics(inputs)
    slot = econ.goal_slots[inputs.goals.index(evening)]
    assert slot is not None
    assert result.schedule.soc_wh[slot] >= inputs.battery.max_wh - 50
    # Before the evening the plan holds the battery (discharge lock) and keeps devices off.
    locks = [t for t in range(slot + 1) if result.plan.intervals[t].battery.discharge_lock]
    assert locks
    assert all(rk.HOLD_FOR_EVENING in _keys(result, t) for t in locks)
    assert all(result.plan.intervals[t].battery.min_power_w == 0.0 for t in locks)
    assert sum(sum(d[: slot + 1]) for d in result.schedule.devices_w) == pytest.approx(0.0)
    throttled = [
        t
        for t in range(slot + 1)
        if any(not band.allowed for band in result.plan.intervals[t].devices.values())
    ]
    assert throttled
    assert all(rk.DEVICE_THROTTLED in _keys(result, t) for t in throttled)


def test_forecast_too_optimistic_still_reaches_the_evening_target() -> None:
    """PV comes in at half the forecast's P50 all day (P10 is a quarter); hourly replanning.

    Review D4 (lead decision, plan §0): caution enters only through the WP7 targets, the device
    budget is planned on P50 plus the overflow a battery-first day cannot absorb. At half the
    P50 the evening target is still met; a whole day at P10 is the WP7 morning buffer's job
    (``docs/plans/phase-4.md`` deviations: with PV at 0.4 x P50 all day the target is missed by
    ~5 %, because the buffer's margin does not count the consumers).
    """
    s = situation("forecast_too_optimistic")
    share = 0.5
    result = replay(s, hours=16, actual_share=share)
    evening = min(result.targets)
    target = result.targets[evening]
    best = battery_first_soc(s, until=evening, actual_share=share)
    assert result.soc_pct[evening] >= min(target, best) - 1.0


def test_a_cautious_risk_level_spends_less_on_devices_than_a_bold_one() -> None:
    s = situation("forecast_too_optimistic")
    cautious = run(situation_inputs(s)).schedule
    bold = run(situation_inputs(replace(s, risk=RiskLevel.BOLD))).schedule
    assert sum(map(sum, cautious.devices_w)) <= sum(map(sum, bold.devices_w)) + EPS


def test_negative_prices_keep_room_in_the_battery_and_avoid_export() -> None:
    inputs = scenario_inputs("negative_prices")
    result = run(inputs)
    negative = [t for t in range(inputs.n) if inputs.import_price[t] < 0]
    assert negative
    # The battery charges inside the negative-price window ...
    charged = sum(result.schedule.pv_charge_w[t] for t in negative) * inputs.dt_h
    assert charged > 5_000
    # ... because it exported earlier at a positive price instead of filling up.
    before = [t for t in range(negative[0]) if inputs.export_price[t] > 0]
    assert sum(result.schedule.export_w[t] for t in before) > 0
    absorbing = [t for t in negative if result.schedule.pv_charge_w[t] > EPS]
    assert absorbing
    assert all(rk.EXPORT_NEGATIVE_PRICE_AVOIDED in _keys(result, t) for t in absorbing)
    assert result.objective < result.heuristic_objective


def test_negative_export_price_is_curtailed_not_paid() -> None:
    s = situation("sunny_day")
    inputs = situation_inputs(
        replace(s, export_price=lambda at: -0.02 if 11 <= at.astimezone(TZ).hour < 15 else 0.08)
    )
    result = run(inputs)
    for t in range(inputs.n):
        if inputs.export_price[t] < 0:
            assert result.schedule.export_w[t] == pytest.approx(0.0, abs=EPS)


def test_price_spike_reserves_the_battery_for_the_spike() -> None:
    inputs = scenario_inputs("price_spike")
    result = run(inputs)
    spike = [t for t in range(inputs.n) if inputs.import_price[t] > 1]
    assert spike
    assert all(result.schedule.battery_load_w[t] >= inputs.load_p50[t] - EPS for t in spike)
    held = [t for t in range(spike[0]) if result.plan.intervals[t].battery.discharge_lock]
    assert held
    assert all(rk.HOLD_FOR_PRICE in _keys(result, t) for t in held)
    reason = next(r for r in result.plan.intervals[held[0]].reasons if r.key == rk.HOLD_FOR_PRICE)
    assert reason.args["later_price"] == pytest.approx(1.5)


def test_grid_charging_only_below_the_price_level_inside_the_schedule() -> None:
    inputs = scenario_inputs("grid_charge_price_level")
    result = run(inputs)
    charged = [t for t in range(inputs.n) if result.schedule.grid_charge_w[t] > EPS]
    assert charged
    for t in range(inputs.n):
        band = result.plan.intervals[t].battery
        hour = _hour(result, t)
        if t in charged:
            assert inputs.import_price[t] <= 0.2
            assert 0 <= hour < 6
            assert band.grid_charge_max_w == pytest.approx(
                result.schedule.grid_charge_w[t], abs=0.1
            )
            reason = next(r for r in result.plan.intervals[t].reasons if r.key == rk.GRID_CHARGE)
            assert reason.args["later_price"] > reason.args["price"]
        else:
            assert band.grid_charge_max_w is None
    # The spread rule: what is charged at 0.15 pays against the later 0.35.
    assert inputs.battery is not None
    assert inputs.battery.model.grid_charge_pays(0.15, 0.35)


def test_grid_charging_needs_the_spread_on_a_dynamic_tariff() -> None:
    s = situation("grid_charge_price_level")
    inputs = situation_inputs(
        replace(s, import_price=lambda at: 0.15 if 1 <= at.astimezone(TZ).hour < 5 else 0.2)
    )
    assert not any(inputs.grid_charge_w)
    assert not any(run(inputs).schedule.grid_charge_w)


def test_grid_charging_stays_off_without_a_policy() -> None:
    s = situation("grid_charge_price_level")
    inputs = situation_inputs(replace(s, grid_charge=type(s.grid_charge)()))
    assert not any(inputs.grid_charge_w)


def test_prefer_full_keeps_the_battery_first_until_max() -> None:
    inputs = scenario_inputs("prefer_full")
    result = run(inputs)
    assert inputs.battery is not None
    assert inputs.battery.strategy is BatteryStrategy.PREFER_FULL
    full = next(
        t for t in range(inputs.n) if result.schedule.soc_wh[t] >= inputs.battery.max_wh - 1
    )
    econ = economics(inputs)
    for t in range(full):
        # Devices only get what the battery cannot take (its charge limit).
        assert (
            result.schedule.device_total_w(t)
            <= max(0.0, econ.surplus_w[t] - inputs.battery.max_charge_w) + EPS
        )
        assert result.plan.intervals[t].battery.priority_until_soc_pct == pytest.approx(
            inputs.battery.max_soc_pct
        )
    assert any(
        band.preference is BandPreference.PREFER_ON
        for interval in result.plan.intervals[full + 1 :]
        for band in interval.devices.values()
    )


def test_reference_house_uses_the_export_limit_and_both_devices() -> None:
    inputs = scenario_inputs("reference_house")
    result = run(inputs)
    assert inputs.export_limit_w == 13_500
    assert max(result.schedule.export_w) <= 13_500 + EPS
    assert all(sum(d) > 0 for d in result.schedule.devices_w)


def test_spot_prices_without_a_formula_are_not_optimised() -> None:
    s = situation("grid_charge_price_level")
    inputs = situation_inputs(replace(s, price_includes="spot_only"))
    assert inputs.price_mode is PriceMode.FLAT
    assert inputs.reasons[0].key == rk.FALLBACK_SPOT_WITHOUT_FORMULA
    assert not any(inputs.grid_charge_w)
    result = run(inputs)
    assert result.plan.reasons[1].key == rk.FALLBACK_SPOT_WITHOUT_FORMULA
    assert result.plan.assumptions["price_mode"] == "flat"


def test_no_prices_plan_for_self_sufficiency() -> None:
    inputs = situation_inputs(replace(situation("sunny_day"), with_prices=False))
    assert inputs.price_mode is PriceMode.FLAT
    assert {r.key for r in inputs.reasons} >= {rk.FALLBACK_NO_PRICES}
    assert run(inputs).source == "solver"


def test_the_plan_covers_48_hours_on_the_quarter_hour_grid() -> None:
    inputs = scenario_inputs("sunny_day")
    plan = run(inputs).plan
    assert len(plan.intervals) == 192
    assert plan.intervals[-1].end - plan.intervals[0].start == timedelta(hours=48)
    assert plan.source == "solver"
    assert plan.created_at == inputs.now


# --- review fixes (PR 4-F) --------------------------------------------------------------------


def _day_one(inputs, t: int) -> bool:  # type: ignore[no-untyped-def]
    return inputs.starts[t].astimezone(TZ).date() == inputs.now.astimezone(TZ).date()


@pytest.mark.parametrize("name", ["reference_house", "prefer_full"])
def test_consumers_get_energy_on_day_one_once_the_battery_is_full(name: str) -> None:
    """Review B1/D4: the device budget includes the overflow a full battery cannot take."""
    inputs = scenario_inputs(name)
    result = run(inputs)
    assert inputs.battery is not None
    full = [
        t
        for t in range(inputs.n)
        if _day_one(inputs, t) and result.schedule.soc_wh[t] >= inputs.battery.max_wh - 1
    ]
    assert full
    assert sum(result.schedule.device_total_w(t) for t in full) > 1_000


def test_the_morning_priority_ends_with_the_buffers_day() -> None:
    """Review m3: ``priority_until_soc_pct`` only until the buffer's goal slot, not 48 h."""
    inputs = scenario_inputs("prefer_full")
    result = run(inputs)
    econ = economics(inputs)
    assert econ.first_until >= 0
    for t, interval in enumerate(result.plan.intervals):
        if t > econ.first_until:
            assert interval.battery is not None
            assert interval.battery.priority_until_soc_pct is None


def test_filled_prices_are_flagged_and_never_cited() -> None:
    """Review m5: median stand-ins are marked and not used as 'later price' in a hold."""
    s = situation("price_spike")
    known_until = s.now + timedelta(hours=10)
    inputs = situation_inputs(
        replace(s, import_price=lambda at: s.import_price(at) if at < known_until else None)
    )
    result = run(inputs)
    holds: list[int] | None = []
    for t, interval in enumerate(result.plan.intervals):
        assert interval.prices is not None
        assert interval.prices.estimated is (inputs.import_known[t] is None)
        for reason in interval.reasons:
            if reason.key != rk.HOLD_FOR_PRICE:
                continue
            later = [
                tau
                for tau in range(t + 1, inputs.n)
                if inputs.starts[tau].astimezone(TZ).strftime("%H:%M") == reason.args["later_time"]
            ]
            assert any(inputs.import_known[tau] is not None for tau in later)
            assert holds is not None
            holds.append(t)
    assert holds, "the spike is inside the known prices: the plan holds for it"


def test_without_a_planner_battery_no_band_is_attached() -> None:
    """Control-safety review m6: no battery in the plan -> the goal rules stay in charge."""
    s = situation("sunny_day")
    inputs = situation_inputs(s)
    inputs = replace(inputs, battery=None, goals=(), morning_buffer_pct=None, grid_charge_w=())
    plan = run(inputs).plan
    assert all(interval.battery is None for interval in plan.intervals)


def test_negative_rule_slots_are_curtailed_not_exported() -> None:
    """Review D3 under the de rule: where the rule zeroed the export price, no export."""
    inputs = scenario_inputs("negative_prices")
    result = run(inputs)
    flagged = [t for t in range(inputs.n) if inputs.discouraged(t)]
    assert flagged
    for t in flagged:
        assert result.schedule.export_w[t] <= EPS
        if econ_surplus(inputs, t) > EPS:
            assert rk.EXPORT_NEGATIVE_PRICE_AVOIDED in _keys(result, t)


def econ_surplus(inputs, t: int) -> float:  # type: ignore[no-untyped-def]
    return max(0.0, inputs.pv_p50[t] - inputs.load_p50[t])


def test_pv_charging_is_not_postponed_without_a_reason() -> None:
    """Review M3: among equal-cost plans the battery charges early (tie-breaker)."""
    s = situation("sunny_day")
    inputs = situation_inputs(replace(s, devices=(), soc_pct=60.0))
    result = run(inputs)
    first_surplus = next(t for t in range(inputs.n) if econ_surplus(inputs, t) > 100)
    first_charge = next(t for t in range(inputs.n) if result.schedule.pv_charge_w[t] > EPS)
    assert first_charge == first_surplus
