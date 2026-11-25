# SPDX-License-Identifier: Apache-2.0
"""The controller's grid layer (PR 6-F): resolver, shedding order, caps, battery support."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from suncore.controller import reasons as rs
from suncore.controller.battery import BatteryIntents
from suncore.controller.grid import (
    CapInput,
    GridHolds,
    GridInputs,
    grid_caps,
    resolve_grid_inputs,
    shed_order,
    situation_reasons,
)
from suncore.controller.levels import Level, LevelTable
from suncore.core.battery import BatteryAction
from suncore.core.decisions import Intent, ReleaseAction
from suncore.core.grid import (
    BackupConfig,
    GridState,
    Para14aBudget,
    Para14aStatus,
    PeakShaving,
)
from suncore.core.site import EntityRef, GridConfig
from tests.controller.test_battery_intents import inputs as battery_inputs
from tests.controller.test_battery_intents import plan as battery_plan
from tests.safety.helpers import continuous_device, steps_device, switch_device

T0 = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)


def table(*powers: float) -> LevelTable:
    return LevelTable(tuple(Level(i, p, None) for i, p in enumerate(powers)))


def cap_input(
    device_id: str, powers: tuple[float, ...], level: int, *, para14a: bool = True, **kw: Any
) -> CapInput:
    t = table(*powers)
    return CapInput(
        device_id=device_id,
        table=t,
        level=level,
        draw_w=kw.pop("draw_w", t.level(level).power_w),
        hysteresis_w=kw.pop("hysteresis_w", 100.0),
        para14a=para14a,
    )


def grid_config(**kw: Any) -> GridConfig:
    return GridConfig(power=EntityRef(entity_id="sensor.grid"), **kw)


# --- resolver ------------------------------------------------------------------------------------


def test_resolver_uses_core_grid_only() -> None:
    config = grid_config(
        import_limit_w=11_000,
        peak_shaving=PeakShaving(enabled=True, peak_floor_w=2500),
        para14a=True,
        para14a_signal=EntityRef(entity_id="binary_sensor.p14a"),
        para14a_limit_w=4200,
        backup=BackupConfig(
            entity_id="sensor.backup", island_states=("island",), keep_devices=("fridge",)
        ),
    )
    out = resolve_grid_inputs(config, month_peak_w=3100, para14a_state="on", backup_state="island")
    assert out.fuse_limit_w == 11_000
    assert out.import_limit_w == 3100
    assert out.peak_limit_w == 3100
    assert out.para14a == Para14aBudget(Para14aStatus.ACTIVE, 4200)
    assert out.grid_state is GridState.ISLAND
    assert out.keep_devices == frozenset({"fridge"})
    assert out.warnings == ()
    keys = [r.key for r in situation_reasons(out)]
    assert keys == [rs.GRID_ISLAND, rs.GRID_PARA14A_ACTIVE, rs.GRID_PEAK_LIMIT]


def test_resolver_unknown_states() -> None:
    config = grid_config(
        para14a=True,
        para14a_signal=EntityRef(entity_id="binary_sensor.p14a"),
        backup=BackupConfig(entity_id="sensor.backup", island_states=("island",)),
    )
    out = resolve_grid_inputs(
        config, month_peak_w=None, para14a_state="unavailable", backup_state=None
    )
    assert out.para14a.status is Para14aStatus.UNKNOWN
    assert out.grid_state is GridState.UNKNOWN  # connected, with a warning
    assert [r.key for r in out.warnings] == [rs.GRID_BACKUP_UNKNOWN]
    assert out.import_limit_w is None


def test_para14a_without_a_signal_warns() -> None:
    out = resolve_grid_inputs(
        grid_config(para14a=True), month_peak_w=None, para14a_state=None, backup_state=None
    )
    assert out.para14a.status is Para14aStatus.INACTIVE
    assert [r.key for r in out.warnings] == [rs.GRID_PARA14A_NO_SIGNAL]
    assert out.grid_state is GridState.NOT_CONFIGURED


@settings(max_examples=200, deadline=None)
@given(
    st.one_of(st.none(), st.floats(0, 50_000)),
    st.booleans(),
    st.floats(0, 20_000),
    st.one_of(st.none(), st.floats(0, 50_000)),
)
def test_effective_limit_never_above_the_fuse(
    fuse: float | None, enabled: bool, floor: float, peak: float | None
) -> None:
    if fuse is not None and enabled and floor > fuse:
        floor = fuse
    config = grid_config(
        import_limit_w=fuse, peak_shaving=PeakShaving(enabled=enabled, peak_floor_w=floor)
    )
    out = resolve_grid_inputs(config, month_peak_w=peak, para14a_state=None, backup_state=None)
    if fuse is not None:
        assert out.import_limit_w is not None
        assert out.import_limit_w <= fuse
    if enabled:
        assert out.import_limit_w is not None
        assert out.import_limit_w >= min(floor, fuse if fuse is not None else floor)


# --- shedding order ------------------------------------------------------------------


def test_shed_order_continuous_first_then_rank_then_reverse_priority() -> None:
    pump = switch_device("pump").model_copy(update={"priority": 5})
    plug = switch_device("plug").model_copy(update={"priority": 30})
    ranked = switch_device("ranked").model_copy(update={"priority": 50, "shed_rank": 1})
    rod = continuous_device("rod").model_copy(update={"priority": 1})
    heater = steps_device().model_copy(update={"priority": 20})
    assert shed_order([pump, plug, ranked, rod, heater]) == [
        "rod",
        "ranked",
        "plug",
        "heater",
        "pump",
    ]


# --- caps ---------------------------------------------------------------------------


def test_island_sheds_all_but_the_keep_list() -> None:
    devices = [cap_input("rod", (0, 1000, 2000), 2), cap_input("fridge", (0, 150), 1)]
    grid = GridInputs(grid_state=GridState.ISLAND, keep_devices=frozenset({"fridge"}))
    caps = grid_caps(devices, grid, GridHolds())
    shed, kept = caps.get("rod"), caps.get("fridge")
    assert shed is not None
    assert shed.level == 0
    assert shed.reason.key == rs.GRID_ISLAND_SHED
    assert kept is not None
    assert kept.level == 1
    assert kept.reason.key == rs.GRID_ISLAND_KEEP


def test_cooldown_freezes_levels() -> None:
    devices = [cap_input("rod", (0, 1000), 0, para14a=False), cap_input("wb", (0, 1000), 1)]
    caps = grid_caps(devices, GridInputs(), GridHolds(island_s=30))
    assert {k: c.level for k, c in caps.caps.items()} == {"rod": 0, "wb": 1}
    caps = grid_caps(devices, GridInputs(), GridHolds(para14a_s=30))
    assert {k: c.level for k, c in caps.caps.items()} == {"wb": 1}
    assert grid_caps(devices, GridInputs(), GridHolds()).caps == {}


def test_unknown_para14a_freezes_flagged_devices() -> None:
    devices = [cap_input("rod", (0, 1000), 0, para14a=False), cap_input("wb", (0, 1000), 0)]
    grid = GridInputs(para14a=Para14aBudget(Para14aStatus.UNKNOWN))
    caps = grid_caps(devices, grid, GridHolds())
    assert set(caps.caps) == {"wb"}
    assert caps.caps["wb"].level == 0
    active = GridInputs(para14a=Para14aBudget(Para14aStatus.ACTIVE, 5000))
    frozen = grid_caps(devices, active, GridHolds(), para14a_fixed_w=None)
    assert frozen.caps["wb"].reason.key == rs.GRID_PARA14A_UNKNOWN


levels_st = st.lists(st.floats(10, 4000), min_size=1, max_size=5).map(lambda xs: (0.0, *sorted(xs)))


@settings(max_examples=400, deadline=None)
@given(
    st.lists(st.tuples(levels_st, st.integers(0, 5), st.floats(0, 500)), min_size=1, max_size=4),
    st.floats(0, 8000),
    st.floats(0, 3000),
)
def test_para14a_caps_keep_the_sum_within_the_budget(
    specs: list[tuple[tuple[float, ...], int, float]], budget: float, fixed: float
) -> None:
    """G3 in the controller: after the caps, the flagged devices' levels fit the budget."""
    devices = [
        cap_input(f"d{i}", powers, min(level, len(powers) - 1), draw_w=0.0)
        for i, (powers, level, _) in enumerate(specs)
    ]
    grid = GridInputs(para14a=Para14aBudget(Para14aStatus.ACTIVE, budget))
    caps = grid_caps(devices, grid, GridHolds(), para14a_fixed_w=fixed)
    total = sum(d.table.level(min(d.level, caps.caps[d.device_id].level)).power_w for d in devices)
    assert total <= max(0.0, budget - fixed) + 1.0 * len(devices)


def test_para14a_dims_the_lower_priority_first() -> None:
    devices = [cap_input("rod", (0, 1000, 2000, 3000), 3), cap_input("wb", (0, 2000), 1)]
    grid = GridInputs(para14a=Para14aBudget(Para14aStatus.ACTIVE, 3500))
    caps = grid_caps(devices, grid, GridHolds())
    assert caps.caps["rod"].level == 3
    assert caps.caps["wb"].level == 0
    assert caps.caps["wb"].reason == rs.para14a_dim(3500)


def test_para14a_cap_does_not_flap_on_noise() -> None:
    """Cloud flicker on the first device's draw: the second device falls at most once."""
    rng = random.Random(7)
    level, changes = 1, 0
    for _ in range(2000):
        noise = rng.uniform(-40, 40)  # well inside the 100 W hysteresis
        devices = [
            cap_input("rod", (0, 2000), 1, draw_w=2000 + noise),
            cap_input("wb", (0, 1450), level),
        ]
        grid = GridInputs(para14a=Para14aBudget(Para14aStatus.ACTIVE, 3480))
        cap = grid_caps(devices, grid, GridHolds()).caps["wb"].level
        new = cap if cap < level else max(level, cap)  # rise at once: the worst case
        if new != level:
            changes += 1
            level = new
    assert changes <= 1


# --- battery support -------------------------------------------------------------------------


def _battery_step(grid: GridInputs, *, deficit: float | None, **band: Any) -> Intent | None:
    return BatteryIntents().step(
        T0,
        battery_plan(T0, **band),
        battery_inputs(under_control=True, held=BatteryAction(max_discharge_w=0.0)),
        50.0,
        grid=grid,
        holds=GridHolds(),
        deficit_w=deficit,
        reserve_w=100.0,
    )


def test_battery_lock_lifted_near_the_import_limit() -> None:
    grid = GridInputs.from_limit(3000)
    intent = _battery_step(grid, deficit=2950, discharge_lock=True)
    assert intent is not None
    assert intent.action == ReleaseAction()
    assert [r.key for r in intent.reasons] == [rs.BATTERY_RELEASE, rs.GRID_BATTERY_SUPPORT]
    unknown = _battery_step(grid, deficit=None, discharge_lock=True)
    assert unknown is not None
    assert unknown.action == ReleaseAction()
    kept = BatteryIntents().step(
        T0,
        battery_plan(T0, discharge_lock=True),
        battery_inputs(),
        50.0,
        grid=grid,
        deficit_w=500,
        reserve_w=100.0,
    )
    assert kept is not None
    assert kept.action == BatteryAction(max_discharge_w=0.0)


def test_battery_released_in_island_and_no_grid_charge_under_para14a() -> None:
    island = GridInputs(grid_state=GridState.ISLAND)
    intent = _battery_step(island, deficit=0, discharge_lock=True)
    assert intent is not None
    assert intent.action == ReleaseAction()
    assert intent.reasons == (rs.plain(rs.BATTERY_RELEASE), rs.plain(rs.GRID_BATTERY_ISLAND))
    dimmed = GridInputs(para14a=Para14aBudget(Para14aStatus.ACTIVE, 4200))
    wanted, reasons, urgent = BatteryIntents.grid_override(
        BatteryAction(grid_charge_w=2000), (), dimmed, GridHolds(), 0, 100
    )
    assert wanted is None
    assert urgent
    assert reasons[0].key == rs.GRID_BATTERY_NO_GRID_CHARGE
    for holds in (GridHolds(island_s=5), GridHolds(para14a_s=5)):
        wanted, _, _ = BatteryIntents.grid_override(
            BatteryAction(grid_charge_w=2000), (), GridInputs(), holds, 0, 100
        )
        assert wanted is None
    same, _, urgent = BatteryIntents.grid_override(
        BatteryAction(grid_charge_w=2000), (), GridInputs(), GridHolds(), 0, 100
    )
    assert same == BatteryAction(grid_charge_w=2000)
    assert not urgent


def test_urgent_release_goes_out_once_then_follows_the_spacing() -> None:
    intents = BatteryIntents()
    island = GridInputs(grid_state=GridState.ISLAND)
    held = battery_inputs(under_control=True, held=BatteryAction(max_discharge_w=0.0))
    first = intents.step(T0, battery_plan(T0, discharge_lock=True), held, 50.0, grid=island)
    assert first is not None
    intents.reset_pending()
    again = intents.step(
        T0 + timedelta(seconds=5), battery_plan(T0, discharge_lock=True), held, 50.0, grid=island
    )
    assert again is None
