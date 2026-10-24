# SPDX-License-Identifier: Apache-2.0
"""Battery invariants B1-B6, B9 (ADR-0013) - property-based.

Every property reads "if :func:`evaluate_battery` lets an action through, then ...": the
claims of the ADR's invariant table must hold for *any* combination of action, adapter,
baseline, live values, plan band, price and import limit hypothesis can construct.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from hypothesis import HealthCheck, Phase, assume, find, given, settings
from hypothesis import strategies as st

from suncore.core.battery import (
    BatteryAction,
    BatteryCapability,
    BatteryModel,
    BatteryRanges,
    FieldRange,
    GridChargePolicy,
    resolve_battery_action,
)
from suncore.core.control import BatteryBand, Plan, PlanInterval
from suncore.core.tariff import WeeklySchedule
from suncore.safety.battery import (
    BatteryContext,
    BatteryLive,
    BatteryWatch,
    can_cause_import,
    evaluate_battery,
    grid_charge_bound_w,
    is_discharge_lock,
    plan_band,
    projected_import_w,
    quantise,
    same_state,
)

NOW = datetime(2026, 10, 24, 2, 0, tzinfo=UTC)
ALL_CAPS = frozenset(BatteryCapability)

power = st.floats(min_value=0, max_value=15_000, allow_nan=False)
maybe_power = st.one_of(st.none(), power)
pct = st.floats(min_value=0, max_value=100, allow_nan=False)
maybe_pct = st.one_of(st.none(), pct)


def rarely[T](draw: st.DrawFn, bad: st.SearchStrategy[T], good: st.SearchStrategy[T]) -> T:
    """``bad`` one time in ten: the strategies stay mostly valid so that accepted actions are
    common (every property is an implication "accepted => claim"), while each failure mode is
    still explored."""
    return draw(bad) if draw(st.integers(0, 9)) == 0 else draw(good)


@st.composite
def actions(draw: st.DrawFn, *, grid: bool = False) -> BatteryAction:
    values: dict[str, Any] = {
        "max_charge_w": rarely(draw, maybe_power, st.none()),
        "max_discharge_w": rarely(draw, maybe_power, st.sampled_from([None, 0.0])),
        "min_soc_pct": rarely(draw, maybe_pct, st.one_of(st.none(), st.floats(20, 100))),
        "grid_charge_w": draw(st.floats(1, 6000))
        if grid
        else rarely(draw, maybe_power, st.one_of(st.none(), st.floats(0, 3000))),
    }
    if all(v is None for v in values.values()):
        values["max_discharge_w"] = 0.0
    if values["grid_charge_w"] is not None and values["max_charge_w"] is not None:
        values["grid_charge_w"] = min(values["grid_charge_w"], values["max_charge_w"])
    return BatteryAction(**values)


@st.composite
def ranges(draw: st.DrawFn) -> BatteryRanges:
    def power_range() -> FieldRange | None:
        return rarely(
            draw,
            st.one_of(st.none(), st.floats(100, 15_000).map(lambda h: FieldRange(0.0, h))),
            st.just(FieldRange(0.0, 10_000.0)),
        )

    soc_range = rarely(
        draw,
        st.none(),
        st.builds(
            FieldRange,
            st.sampled_from([0.0, 5.0]),
            st.just(100.0),
            st.sampled_from([None, 1.0, 5.0]),
        ),
    )
    return BatteryRanges(
        max_charge_w=power_range(),
        max_discharge_w=power_range(),
        min_soc_pct=soc_range,
        grid_charge_w=power_range(),
    )


@st.composite
def bands(draw: st.DrawFn) -> BatteryBand | None:
    if draw(st.integers(0, 5)) == 0:
        return None
    grid = rarely(draw, st.one_of(st.none(), power), st.floats(2000, 5000))
    max_soc = draw(st.one_of(st.none(), st.floats(50, 100)))
    return BatteryBand(
        grid_charge_max_w=grid,
        discharge_lock=rarely(draw, st.just(False), st.just(True)),
        min_soc_pct=None,
        max_soc_pct=max_soc,
    )


@st.composite
def contexts(draw: st.DrawFn, *, grid: bool = False) -> BatteryContext:
    model = BatteryModel(
        capacity_wh=10_000,
        max_charge_w=draw(st.floats(1000, 12_000)),
        max_discharge_w=draw(st.floats(1000, 12_000)),
        reserve_pct=draw(st.floats(0, 15)),
        bms_drift_margin_pct=draw(st.floats(0, 5)),
        max_soc_pct=draw(st.floats(60, 100)),
    )
    reserve = model.min_soc_pct + draw(st.sampled_from([0.0, 5.0]))
    requested = draw(actions(grid=grid))
    baseline = rarely(draw, actions(), st.none())
    rng = draw(ranges())
    target = quantise(resolve_battery_action(requested, baseline), rng)
    policy = rarely(
        draw,
        st.sampled_from(
            [
                GridChargePolicy(),
                GridChargePolicy(enabled=True, schedule=WeeklySchedule.daily("22:00", "06:00")),
            ]
        ),
        st.just(GridChargePolicy(enabled=True, max_price=0.20)),
    )
    caps = rarely(draw, st.frozensets(st.sampled_from(list(BatteryCapability))), st.just(ALL_CAPS))
    return BatteryContext(
        requested=requested,
        target=target,
        capabilities=caps,
        ranges=rng,
        model=model if draw(st.integers(0, 9)) else None,
        reserve_pct=reserve,
        live=BatteryLive(
            soc_pct=rarely(draw, maybe_pct, st.floats(10, 60)),
            grid_w=rarely(
                draw, st.one_of(st.none(), st.floats(-10_000, 20_000)), st.floats(-3000, 3000)
            ),
            battery_w=rarely(
                draw, st.one_of(st.none(), st.floats(-8000, 8000)), st.floats(-3000, 3000)
            ),
        ),
        band=draw(bands()),
        plan_fresh=rarely(draw, st.just(False), st.just(True)),
        policy=policy,
        import_price=rarely(draw, st.one_of(st.none(), st.floats(-0.5, 0.8)), st.floats(-0.1, 0.2)),
        tz=UTC,
        import_limit_w=rarely(
            draw, st.one_of(st.none(), st.floats(0, 30_000)), st.floats(8000, 30_000)
        ),
        reserve_w=100.0,
        now=NOW + timedelta(hours=draw(st.integers(0, 23))),
        baseline=baseline,
    )


def accepted(ctx: BatteryContext) -> bool:
    return evaluate_battery(ctx) == ()


PROPERTY = settings(
    max_examples=150, deadline=None, suppress_health_check=[HealthCheck.filter_too_much]
)


@PROPERTY
@given(contexts())
def test_b9_no_field_without_its_capability(ctx: BatteryContext) -> None:
    assume(accepted(ctx))
    names = {
        "max_charge_w": BatteryCapability.CHARGE_LIMIT,
        "max_discharge_w": BatteryCapability.DISCHARGE_LIMIT,
        "min_soc_pct": BatteryCapability.MIN_SOC,
        "grid_charge_w": BatteryCapability.GRID_CHARGE,
    }
    for name, cap in names.items():
        if getattr(ctx.requested, name) is not None:
            assert cap in ctx.capabilities


@PROPERTY
@given(contexts())
def test_b1_a_written_floor_is_never_below_the_reserve_or_the_baseline(ctx: BatteryContext) -> None:
    assume(accepted(ctx))
    assert ctx.model is not None
    assert ctx.reserve_pct is not None
    if ctx.requested.min_soc_pct is not None:
        assert ctx.target.min_soc_pct is not None
        assert ctx.target.min_soc_pct >= ctx.reserve_pct - 0.01
        if ctx.baseline is not None and ctx.baseline.min_soc_pct is not None:
            assert ctx.target.min_soc_pct >= ctx.baseline.min_soc_pct - 0.01


@PROPERTY
@given(contexts())
def test_suncore_never_widens_the_discharge_of_the_users_settings(ctx: BatteryContext) -> None:
    """B1 + B6: every accepted limit restricts discharge at least as much as the baseline."""
    assume(accepted(ctx))
    base = ctx.baseline
    if (
        ctx.requested.max_discharge_w is not None
        and base is not None
        and base.max_discharge_w is not None
    ):
        assert ctx.target.max_discharge_w is not None
        assert ctx.target.max_discharge_w <= base.max_discharge_w + 1.0


@PROPERTY
@given(contexts(grid=True))
def test_b2_grid_charging_within_every_bound(ctx: BatteryContext) -> None:
    assume(accepted(ctx))
    grid = ctx.target.grid_charge_w or 0.0
    assume(grid > 0)
    model, band = ctx.model, ctx.band
    assert model is not None
    assert band is not None
    assert band.grid_charge_max_w is not None
    assert ctx.import_limit_w is not None
    assert ctx.live.deficit_w is not None
    assert ctx.ranges.grid_charge_w is not None
    assert grid <= model.max_charge_w + 1.0
    assert grid <= band.grid_charge_max_w + 1.0
    assert grid <= ctx.ranges.grid_charge_w.high + 1.0
    assert grid <= ctx.import_limit_w - ctx.live.deficit_w - ctx.reserve_w + 1.0
    bound = grid_charge_bound_w(ctx)
    assert bound is not None
    assert grid <= bound + 1.0


@PROPERTY
@given(contexts(grid=True))
def test_b3_grid_charging_only_planned_fresh_in_policy_and_below_max(ctx: BatteryContext) -> None:
    assume(accepted(ctx))
    assume((ctx.target.grid_charge_w or 0.0) > 0)
    assert ctx.plan_fresh
    assert ctx.band is not None
    assert (ctx.band.grid_charge_max_w or 0) > 0
    assert ctx.policy.allows(ctx.now, ctx.import_price, ctx.tz)
    assert ctx.import_price is not None or ctx.policy.max_price is None
    assert ctx.live.soc_pct is not None
    assert ctx.model is not None
    assert ctx.live.soc_pct < ctx.model.max_soc_pct


@PROPERTY
@given(contexts())
def test_b4_a_discharge_lock_only_from_a_fresh_planned_lock(ctx: BatteryContext) -> None:
    assume(accepted(ctx))
    if is_discharge_lock(ctx):
        assert ctx.band is not None
        assert ctx.band.discharge_lock
        assert ctx.plan_fresh


@PROPERTY
@given(contexts())
def test_b5_the_import_limit_holds_for_every_action_that_can_cause_import(
    ctx: BatteryContext,
) -> None:
    assume(accepted(ctx))
    if can_cause_import(ctx) and ctx.import_limit_w is not None:
        projected = projected_import_w(ctx)
        assert projected is not None
        assert projected <= ctx.import_limit_w + 1.0


@PROPERTY
@given(contexts())
def test_b6_every_requested_value_is_inside_the_live_range(ctx: BatteryContext) -> None:
    assume(accepted(ctx))
    for name in ("max_charge_w", "max_discharge_w", "min_soc_pct", "grid_charge_w"):
        if getattr(ctx.requested, name) is None:
            continue
        allowed = ctx.ranges.of(name)
        assert allowed is not None
        value = getattr(ctx.target, name)
        assert allowed.low - 1.0 <= value <= allowed.high + 1.0


@PROPERTY
@given(contexts())
def test_without_a_model_nothing_passes(ctx: BatteryContext) -> None:
    if ctx.model is None:
        assert not accepted(ctx)


# --- examples ---------------------------------------------------------------------------------


def _model() -> BatteryModel:
    return BatteryModel(capacity_wh=10_000, max_charge_w=5000, max_discharge_w=5000)


def _ctx(**kw: Any) -> BatteryContext:
    requested = kw.pop("requested")
    baseline = kw.pop("baseline", None)
    rng = kw.pop(
        "ranges",
        BatteryRanges(
            max_charge_w=FieldRange(0, 5000),
            max_discharge_w=FieldRange(0, 5000),
            min_soc_pct=FieldRange(5, 100, 5),
            grid_charge_w=FieldRange(0, 5000),
        ),
    )
    base: dict[str, Any] = {
        "requested": requested,
        "target": quantise(resolve_battery_action(requested, baseline), rng),
        "capabilities": ALL_CAPS,
        "ranges": rng,
        "model": _model(),
        "reserve_pct": 15.0,
        "live": BatteryLive(soc_pct=40, grid_w=600, battery_w=0),
        "band": BatteryBand(grid_charge_max_w=3000, discharge_lock=True),
        "plan_fresh": True,
        "policy": GridChargePolicy(enabled=True, max_price=0.20),
        "import_price": 0.15,
        "tz": UTC,
        "import_limit_w": 10_000,
        "reserve_w": 100.0,
        "now": NOW,
        "baseline": baseline,
    }
    base.update(kw)
    return BatteryContext(**base)


def keys(ctx: BatteryContext) -> list[str]:
    return [r.key for r in evaluate_battery(ctx)]


def test_a_planned_grid_charge_in_policy_passes() -> None:
    assert keys(_ctx(requested=BatteryAction(grid_charge_w=3000))) == []


@pytest.mark.parametrize(
    ("change", "key"),
    [
        ({"band": None}, "safety.battery_grid_charge_unplanned"),
        ({"band": BatteryBand()}, "safety.battery_grid_charge_unplanned"),
        ({"plan_fresh": False}, "safety.battery_plan_stale"),
        ({"import_price": 0.30}, "safety.battery_grid_charge_policy"),
        ({"import_price": None}, "safety.battery_grid_charge_policy"),
        (
            {"live": BatteryLive(soc_pct=None, grid_w=600, battery_w=0)},
            "safety.battery_soc_unknown",
        ),
        ({"live": BatteryLive(soc_pct=100, grid_w=600, battery_w=0)}, "safety.battery_full"),
        ({"import_limit_w": None}, "safety.battery_import_limit_unknown"),
        (
            {"live": BatteryLive(soc_pct=40, grid_w=None, battery_w=0)},
            "safety.battery_grid_unknown",
        ),
        ({"import_limit_w": 3500}, "safety.battery_grid_charge_bound"),
        ({"model": None}, "safety.battery_model_missing"),
        (
            {"capabilities": frozenset({BatteryCapability.MIN_SOC})},
            "safety.battery_capability_missing",
        ),
    ],
)
def test_grid_charge_rejections(change: dict[str, Any], key: str) -> None:
    assert key in keys(_ctx(requested=BatteryAction(grid_charge_w=3000), **change))


def test_the_import_headroom_counts_the_house_without_the_battery() -> None:
    # 600 W import of which 3000 W are the battery already charging from the grid: the house
    # alone exports 2400 W - the headroom is the full limit minus the reserve.
    ctx = _ctx(
        requested=BatteryAction(grid_charge_w=3000),
        live=BatteryLive(soc_pct=40, grid_w=600, battery_w=3000),
        import_limit_w=3200,
    )
    assert keys(ctx) == []
    assert grid_charge_bound_w(ctx) == pytest.approx(3000)


def test_min_soc_rounds_up_and_never_below_the_reserve_or_the_users_floor() -> None:
    lock = _ctx(requested=BatteryAction(min_soc_pct=41))
    assert lock.target.min_soc_pct == 45
    assert keys(lock) == []
    assert keys(_ctx(requested=BatteryAction(min_soc_pct=10))) == [
        "safety.battery_min_soc_below_reserve"
    ]
    assert keys(
        _ctx(requested=BatteryAction(min_soc_pct=20), baseline=BatteryAction(min_soc_pct=30))
    ) == ["safety.battery_min_soc_below_baseline"]


def test_a_discharge_lock_needs_a_planned_lock() -> None:
    unplanned = _ctx(requested=BatteryAction(max_discharge_w=0), band=BatteryBand())
    assert keys(unplanned) == ["safety.battery_unplanned_import"]
    assert keys(_ctx(requested=BatteryAction(max_discharge_w=0))) == []
    # 600 W deficit, a cap of 1000 W is no lock now (the battery still covers the house).
    assert keys(_ctx(requested=BatteryAction(max_discharge_w=1000), band=BatteryBand())) == []
    stale = _ctx(requested=BatteryAction(max_discharge_w=0), plan_fresh=False)
    assert keys(stale) == ["safety.battery_plan_stale"]


def test_a_lock_that_would_break_the_import_limit_is_rejected() -> None:
    ctx = _ctx(
        requested=BatteryAction(max_discharge_w=0),
        live=BatteryLive(soc_pct=40, grid_w=0, battery_w=-6000),
        import_limit_w=5000,
    )
    assert keys(ctx) == ["safety.battery_import_limit"]


def test_ranges_and_baseline_caps() -> None:
    assert keys(_ctx(requested=BatteryAction(max_charge_w=6000))) == ["safety.battery_out_of_range"]
    no_range = BatteryRanges()
    assert keys(_ctx(requested=BatteryAction(max_charge_w=1000), ranges=no_range)) == [
        "safety.battery_range_unknown"
    ]
    assert keys(
        _ctx(
            requested=BatteryAction(max_charge_w=3000),
            baseline=BatteryAction(max_charge_w=2000),
        )
    ) == ["safety.battery_above_baseline"]


def test_plan_freshness() -> None:
    start = NOW.replace(minute=0)
    interval = PlanInterval(
        start=start, end=start + timedelta(minutes=15), battery=BatteryBand(discharge_lock=True)
    )
    plan = Plan(created_at=start - timedelta(minutes=5), intervals=(interval,))
    band, fresh = plan_band(plan, start + timedelta(minutes=5))
    assert band is not None
    assert band.discharge_lock
    assert fresh
    old = plan.model_copy(update={"created_at": start - timedelta(minutes=31)})
    assert plan_band(old, start + timedelta(minutes=5))[1] is False
    assert plan_band(Plan.default(), start) == (None, False)
    assert plan_band(plan, start + timedelta(minutes=20)) == (None, False)


def test_same_state_compares_the_written_fields() -> None:
    a = BatteryAction(max_discharge_w=0, min_soc_pct=45)
    assert same_state(a, BatteryAction(max_discharge_w=0.5, min_soc_pct=45), ["max_discharge_w"])
    assert not same_state(a, BatteryAction(max_discharge_w=0), ["max_discharge_w", "min_soc_pct"])
    assert not same_state(a, None, ["max_discharge_w"])


# --- watchdog (B8) ----------------------------------------------------------------------------


def test_a_frozen_battery_power_while_the_grid_moves_is_a_fault() -> None:
    watch = BatteryWatch(frozen_s=300, grid_move_w=500)
    reason = None
    for i in range(80):
        now = NOW + timedelta(seconds=5 * i)
        grid = 200.0 + (800.0 if i % 20 < 10 else 0.0)
        reason = watch.check(now, BatteryLive(soc_pct=50, grid_w=grid, battery_w=-1200), 10_000)
        if reason is not None:
            break
    assert reason is not None
    assert reason.key == "safety.battery_frozen"


def test_a_constant_battery_power_with_a_steady_grid_is_fine() -> None:
    watch = BatteryWatch(frozen_s=300)
    for i in range(200):
        now = NOW + timedelta(seconds=5 * i)
        live = BatteryLive(soc_pct=50 - i * 0.05, grid_w=10.0, battery_w=-1200)
        assert watch.check(now, live, 10_000) is None


def test_an_idle_battery_at_zero_is_never_frozen() -> None:
    watch = BatteryWatch(frozen_s=60)
    for i in range(200):
        now = NOW + timedelta(seconds=5 * i)
        grid = 1000.0 if i % 2 else -1000.0
        assert watch.check(now, BatteryLive(soc_pct=100, grid_w=grid, battery_w=0), 10_000) is None


def test_a_frozen_soc_while_the_battery_moves_energy_is_a_fault() -> None:
    watch = BatteryWatch(soc_energy_pct=3.0)
    reason = None
    for i in range(1000):
        now = NOW + timedelta(seconds=5 * i)
        power = -2000.0 + (i % 3)  # moving power: not frozen
        reason = watch.check(now, BatteryLive(soc_pct=60, grid_w=100, battery_w=power), 10_000)
        if reason is not None:
            elapsed_h = 5 * i / 3600
            assert elapsed_h * 2000 >= 300  # 3 % of 10 kWh moved before the fault
            break
    assert reason is not None
    assert reason.key == "safety.battery_soc_frozen"


@given(st.lists(st.floats(-5000, 5000), min_size=1, max_size=50))
def test_the_watch_never_fires_without_a_capacity_or_on_moving_values(values: list[float]) -> None:
    watch = BatteryWatch(frozen_s=10_000)
    for i, v in enumerate(values):
        now = NOW + timedelta(seconds=5 * i)
        assert watch.check(now, BatteryLive(soc_pct=50, grid_w=v, battery_w=v + i), None) is None


def test_the_generator_produces_accepted_actions_of_every_kind() -> None:
    """The implications above are not vacuous: accepted grid charging, locks and floors exist."""
    quick = settings(phases=[Phase.generate], database=None, max_examples=500)
    assert find(
        contexts(grid=True),
        lambda c: accepted(c) and (c.target.grid_charge_w or 0) > 0,
        settings=quick,
    )
    assert find(contexts(), lambda c: accepted(c) and is_discharge_lock(c), settings=quick)
    assert find(
        contexts(), lambda c: accepted(c) and c.requested.min_soc_pct is not None, settings=quick
    )
