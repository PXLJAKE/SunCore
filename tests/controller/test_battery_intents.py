# SPDX-License-Identifier: Apache-2.0
"""The plan's battery band -> BatteryAction intents (WP10): mapping, hysteresis, rate, release."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from suncore.controller import Controller, ControllerInputs
from suncore.controller.battery import (
    BatteryControlInputs,
    BatteryControlSettings,
    BatteryIntents,
)
from suncore.controller.surplus import SurplusTracker
from suncore.core.battery import (
    BATTERY_ASSET_ID,
    BatteryAction,
    BatteryCapability,
    BatteryModel,
    BatteryRanges,
    FieldRange,
)
from suncore.core.config import GoalsConfig
from suncore.core.control import BatteryBand, Plan, PlanInterval
from suncore.core.decisions import Decision, DecisionKind, Intent, OperatingMode, ReleaseAction
from tests.safety.helpers import flows

T0 = datetime(2026, 10, 24, 2, 0, tzinfo=UTC)
MODEL = BatteryModel(capacity_wh=10_000, max_charge_w=5000, max_discharge_w=5000)
FULL = BatteryRanges(
    max_charge_w=FieldRange(0, 5000),
    max_discharge_w=FieldRange(0, 5000),
    min_soc_pct=FieldRange(5, 100, 5),
    grid_charge_w=FieldRange(0, 5000),
)
CAPS = frozenset(BatteryCapability) - {BatteryCapability.HARDWARE_FALLBACK}
REST_CAPS = frozenset({BatteryCapability.MIN_SOC})
REST_RANGES = BatteryRanges(min_soc_pct=FieldRange(5, 100, 5))


def plan(at: datetime = T0, *, created: datetime | None = None, **band: Any) -> Plan:
    start = at.replace(minute=(at.minute // 15) * 15, second=0, microsecond=0)
    return Plan(
        created_at=created if created is not None else start,
        intervals=(
            PlanInterval(
                start=start, end=start + timedelta(minutes=15), battery=BatteryBand(**band)
            ),
        ),
    )


def inputs(**kw: Any) -> BatteryControlInputs:
    base: dict[str, Any] = {
        "capabilities": CAPS,
        "ranges": FULL,
        "model": MODEL,
        "reserve_pct": 15.0,
    }
    base.update(kw)
    return BatteryControlInputs(**base)


def decide(intent: Intent, kind: DecisionKind) -> Decision:
    return Decision(intent=intent, kind=kind, ts=intent.ts, mode=OperatingMode.AUTOMATIC)


def keys(planner: BatteryIntents) -> list[str]:
    assert planner.view is not None
    return [r.key for r in planner.view.reasons]


def test_grid_charging_is_rounded_down_and_capped() -> None:
    p = BatteryIntents()
    intent = p.step(T0, plan(grid_charge_max_w=3456.7), inputs(), 30)
    assert intent is not None
    assert intent.asset_id == BATTERY_ASSET_ID
    assert intent.action == BatteryAction(grid_charge_w=3400)
    assert keys(p) == ["controller.battery.grid_charge"]
    big = BatteryIntents().step(T0, plan(grid_charge_max_w=9000), inputs(), 30)
    assert big is not None
    assert big.action == BatteryAction(grid_charge_w=5000)


def test_grid_charging_without_the_capability_is_shown_not_asked() -> None:
    p = BatteryIntents()
    assert p.step(T0, plan(grid_charge_max_w=3000), inputs(capabilities=REST_CAPS), 30) is None
    assert keys(p) == ["controller.battery.grid_charge_unsupported"]
    switch = inputs(ranges=BatteryRanges(grid_charge_w=FieldRange(5000, 5000)))
    assert BatteryIntents().step(T0, plan(grid_charge_max_w=3000), switch, 30) is None
    full = BatteryIntents().step(T0, plan(grid_charge_max_w=5000), switch, 30)
    assert full is not None
    assert full.action == BatteryAction(grid_charge_w=5000)


def test_discharge_lock_by_discharge_cap_or_by_min_soc() -> None:
    capped = BatteryIntents().step(T0, plan(discharge_lock=True), inputs(), 42)
    assert capped is not None
    assert capped.action == BatteryAction(max_discharge_w=0)
    floor = BatteryIntents().step(
        T0, plan(discharge_lock=True), inputs(capabilities=REST_CAPS, ranges=REST_RANGES), 42
    )
    assert floor is not None
    assert floor.action == BatteryAction(min_soc_pct=45)  # rounded up to the step
    low = BatteryIntents().step(
        T0, plan(discharge_lock=True), inputs(capabilities=REST_CAPS, ranges=REST_RANGES), 8
    )
    assert low is not None
    assert low.action == BatteryAction(min_soc_pct=15)  # never below the reserve
    p = BatteryIntents()
    assert p.step(T0, plan(discharge_lock=True), inputs(capabilities=frozenset()), 42) is None
    assert keys(p) == ["controller.battery.discharge_lock_unsupported"]


def test_a_min_soc_lock_moves_only_a_full_step_after_the_soc() -> None:
    p = BatteryIntents(BatteryControlSettings(interval_s=0))
    rest = {"capabilities": REST_CAPS, "ranges": REST_RANGES}
    held = BatteryAction(min_soc_pct=45)
    for soc in (42.0, 44.0, 45.0, 47.0, 49.9):
        assert p.wanted(T0, plan(discharge_lock=True), inputs(held=held, **rest), soc)[0] == held
    moved = p.wanted(T0, plan(discharge_lock=True), inputs(held=held, **rest), 50.2)[0]
    assert moved == BatteryAction(min_soc_pct=55)


def test_nothing_planned_releases_only_what_the_gate_holds() -> None:
    p = BatteryIntents()
    assert p.step(T0, plan(), inputs(), 40) is None
    released = p.step(
        T0, plan(), inputs(under_control=True, held=BatteryAction(max_discharge_w=0)), 40
    )
    assert released is not None
    assert isinstance(released.action, ReleaseAction)
    assert keys(p)[0] == "controller.battery.release"


def test_a_stale_or_default_plan_asks_for_nothing() -> None:
    stale = plan(discharge_lock=True, created=T0 - timedelta(hours=1))
    p = BatteryIntents()
    assert p.step(T0, stale, inputs(), 40) is None
    assert keys(p) == ["controller.battery.no_plan"]
    assert BatteryIntents().step(T0, Plan.default(), inputs(), 40) is None


def test_no_intent_while_blocked_or_while_the_gate_holds_it() -> None:
    from suncore.core.decisions import Reason  # noqa: PLC0415

    p = BatteryIntents()
    blocked = inputs(blocked=Reason(key="safety.emergency_stop"))
    assert p.step(T0, plan(discharge_lock=True), blocked, 40) is None
    held = inputs(under_control=True, held=BatteryAction(max_discharge_w=0))
    assert BatteryIntents().step(T0, plan(discharge_lock=True), held, 40) is None


def test_hysteresis_keeps_the_held_grid_power() -> None:
    p = BatteryIntents()
    held = inputs(under_control=True, held=BatteryAction(grid_charge_w=3000))
    for planned in (2800, 3100, 3250, 2900):
        assert p.wanted(T0, plan(grid_charge_max_w=planned), held, 30)[0] == BatteryAction(
            grid_charge_w=3000
        )
    assert p.wanted(T0, plan(grid_charge_max_w=3400), held, 30)[0] == BatteryAction(
        grid_charge_w=3400
    )


def test_one_intent_per_interval_and_repeats_only_after_repeat_s() -> None:
    p = BatteryIntents(BatteryControlSettings(interval_s=60, repeat_s=900))
    now = T0
    first = p.step(now, plan(now, discharge_lock=True), inputs(), 40)
    assert first is not None
    p.report(decide(first, DecisionKind.SHADOW))
    count = 1
    for _ in range(170):  # 14 min of 5 s ticks, same wish in shadow
        now += timedelta(seconds=5)
        if p.step(now, plan(now, discharge_lock=True), inputs(), 40) is not None:
            count += 1
    assert count == 1
    now += timedelta(seconds=70)
    again = p.step(now, plan(now, discharge_lock=True), inputs(), 40)
    assert again is not None  # after repeat_s the shadow "would have" is shown again


def test_a_pending_intent_blocks_new_ones_until_reported_or_timed_out() -> None:
    p = BatteryIntents(BatteryControlSettings(interval_s=0, pending_timeout_s=300))
    first = p.step(T0, plan(discharge_lock=True), inputs(), 40)
    assert first is not None
    later = T0 + timedelta(seconds=30)
    assert p.step(later, plan(later, grid_charge_max_w=2000), inputs(), 40) is None
    much_later = T0 + timedelta(seconds=301)
    assert p.step(much_later, plan(much_later, grid_charge_max_w=2000), inputs(), 40) is not None


@settings(max_examples=60, deadline=None)
@given(
    st.lists(
        st.tuples(
            st.sampled_from(["lock", "grid", "none"]),
            st.floats(500, 6000),
            st.floats(5, 100),
            st.sampled_from(list(DecisionKind)),
        ),
        min_size=20,
        max_size=400,
    )
)
def test_flapping_bands_never_oscillate_the_battery(
    ticks: list[tuple[str, float, float, DecisionKind]],
) -> None:
    """Whatever the plan and the gate do every 5 s: at most one battery intent per interval,
    every floor >= reserve, every grid power within the band and the battery's limit."""
    p = BatteryIntents(BatteryControlSettings(interval_s=60))
    now = T0
    last: datetime | None = None
    held: BatteryAction | None = None
    for kind, power, soc, verdict in ticks:
        now += timedelta(seconds=5)
        band = {"lock": {"discharge_lock": True}, "grid": {"grid_charge_max_w": power}}
        current = plan(now, **band.get(kind, {}))
        intent = p.step(now, current, inputs(held=held, under_control=held is not None), soc)
        if intent is None:
            continue
        if last is not None:
            assert now - last >= timedelta(seconds=60)
        last = now
        action = intent.action
        if isinstance(action, BatteryAction):
            if action.min_soc_pct is not None:
                assert action.min_soc_pct >= 15
            if action.grid_charge_w is not None:
                assert action.grid_charge_w <= min(power, 5000) + 1
        p.report(decide(intent, verdict))
        if verdict is DecisionKind.ACCEPTED:
            held = action if isinstance(action, BatteryAction) else None


def test_controller_submits_the_battery_intent_with_the_devices() -> None:
    controller = Controller([])
    result = controller.step(
        T0,
        ControllerInputs(
            flows=flows(grid_w=500, battery_w=0, soc_pct=40),
            plan=plan(discharge_lock=True),
            battery=inputs(),
        ),
    )
    assert [i.asset_id for i in result.intents] == [BATTERY_ASSET_ID]
    assert result.battery is not None
    assert result.battery.wanted == BatteryAction(max_discharge_w=0)
    assert controller.report(decide(result.intents[0], DecisionKind.ACCEPTED))
    no_battery = Controller([]).step(T0, ControllerInputs(plan=plan(discharge_lock=True)))
    assert no_battery.intents == ()
    assert no_battery.battery is None


def test_grid_charging_power_is_never_surplus_for_consumers() -> None:
    goals = GoalsConfig(climate_before_export=True)
    tracker = SurplusTracker(60)
    snapshot = flows(grid_w=3000, battery_w=3000, soc_pct=40)
    charging = tracker.update(T0, snapshot, goals, grid_charging=True)
    assert not charging.charging_available
    assert charging.latest_site_w == -3000  # the import, not the battery's charge
    plain = SurplusTracker(60).update(T0, snapshot, goals)
    assert plain.charging_available
