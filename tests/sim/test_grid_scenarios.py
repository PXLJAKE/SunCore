# SPDX-License-Identifier: Apache-2.0
"""The grid scenarios of PR 6-F, closed-loop (plan WP6): fuse shedding, peak shaving across a
month boundary, §14a dimming with cloud flicker and the grid outage ("Netzausfall").

Judged by physical evidence (device power, house balance) and the gate's decisions: no device is
raised while the house imports above the limit, island or §14a rules hold, the cool-down holds
after each end, switching stays within the limits and nothing oscillates.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from suncore.core.control import ControlSettings
from suncore.core.decisions import DecisionKind, ReleaseAction
from suncore.core.grid import GridState, month_key
from suncore.sim.runner import RunResult
from suncore.sim.scenario import load_scenario
from tests.sim.helpers import run_named, run_scenario

pytestmark = pytest.mark.simulator

SETTINGS = ControlSettings()
GRID_SCENARIOS = [
    "import_limit_fuse_shed",
    "peak_shaving_month",
    "para14a_dimming",
    "grid_outage_island",
]


def at(result: RunResult, offset_s: float) -> int:
    return int(offset_s // result.step_s)


def offset(result: RunResult, when: object) -> float:
    return (when - result.scenario.start).total_seconds()  # type: ignore[operator]


def rises(result: RunResult, device_id: str) -> list[float]:
    """Offsets of the accepted writes that raised the device's physical power."""
    out: list[float] = []
    for d in result.accepted(device_id):
        if isinstance(d.intent.action, ReleaseAction):
            continue
        i = at(result, offset(result, d.intent.ts))
        before = result.devices[device_id].power_w[max(0, i - 1)]
        after = result.devices[device_id].power_w[min(len(result.steps) - 1, i + 3)]
        if after > before + 1.0:
            out.append(offset(result, d.intent.ts))
    return out


@pytest.mark.parametrize("name", GRID_SCENARIOS)
def test_switching_stays_within_the_limits_and_nothing_is_rejected(name: str) -> None:
    result, _ = run_named(name)
    for device_id, config in result.configs.items():
        limit = config.max_switches_per_hour or SETTINGS.default_max_switches_per_hour
        assert result.switches_in_window(device_id) <= limit, device_id
    # The controller mirrors the gate's grid rules: it proposes nothing the gate refuses.
    assert result.of_kind(DecisionKind.REJECTED) == []
    assert result.of_kind(DecisionKind.FAILED) == []


# --- fuse shedding ---------------------------------------------------------------------------


def test_fuse_shed_order_and_min_run_waiver() -> None:
    result, _ = run_named("import_limit_fuse_shed")
    fuse = result.scenario.control.import_limit_w
    assert fuse == 3000
    sheds = [
        d
        for d in result.accepted()
        if any(r.key == "controller.grid.fuse_shed" for r in d.intent.reasons)
    ]
    # The continuous rod first, then the pump (shedding order, ADR-0018 decision 2).
    assert [d.intent.asset_id for d in sheds] == ["rod", "pool"]
    # The pump ran less than its 30 min minimum: waived above the fuse limit (G2).
    pool_on = [i for i, on in result.transitions("pool") if on]
    pool_off = [i for i, on in result.transitions("pool") if not on]
    assert pool_on
    assert pool_off
    run_s = (pool_off[0] - pool_on[0]) * result.step_s
    assert run_s < result.configs["pool"].min_run_s
    # The import above the fuse ends within a minute of the spike.
    spike = 1800
    over = [
        i * result.step_s
        for i, s in enumerate(result.steps)
        if s.grid_w > fuse and i * result.step_s >= spike
    ]
    assert over
    assert max(over) - spike <= 60
    # Nothing is raised while the house imports above the limit.
    for device_id in ("rod", "pool"):
        for t in rises(result, device_id):
            assert result.steps[at(result, t)].grid_w <= fuse


# --- peak shaving across the month boundary ------------------------------------------------------


def test_peak_shaving_sheds_without_waiving_min_run() -> None:
    result, _ = run_named("peak_shaving_month")
    sheds = [
        d
        for d in result.accepted()
        if any(r.key == "controller.grid.shed" for r in d.intent.reasons)
    ]
    assert sheds
    assert sheds[0].intent.asset_id == "rod"
    assert not any(
        r.key == "controller.grid.fuse_shed" for d in result.accepted() for r in d.intent.reasons
    )
    for device_id in ("rod", "pool"):
        for t in rises(result, device_id):
            assert result.steps[at(result, t)].grid_w <= 2500 + 1


def test_month_peak_resets_at_local_midnight() -> None:
    scenario = load_scenario("peak_shaving_month")
    tz = scenario.start.tzinfo
    assert tz is not None
    july, runner = run_scenario(scenario, duration_s=9.5 * 3600)  # until 23:30 on 31 July
    assert runner.stack is not None
    end = scenario.start + timedelta(seconds=9.5 * 3600)
    peak = runner.stack.grid_monitor.month_peak_w(end)
    assert month_key(end, tz) == "2026-07"
    assert peak is not None
    # The highest 15-minute mean import of July, recomputed from the house balance.
    quarters: dict[int, list[float]] = {}
    for step in july.steps:
        quarters.setdefault(int(step.t.timestamp()) // 900, []).append(max(0.0, step.grid_w))
    means = [sum(v) / len(v) for k, v in quarters.items() if len(v) * july.step_s >= 900]
    assert peak == pytest.approx(max(means), abs=60.0)
    full, runner = run_scenario(scenario)
    assert runner.stack is not None
    end = scenario.start + timedelta(seconds=scenario.duration_s)
    assert month_key(end, tz) == "2026-08"
    august = runner.stack.grid_monitor.month_peak_w(end)
    assert august is not None
    assert august < peak  # a fresh month: only the night's base load
    assert runner.stack.grid is not None
    assert runner.stack.grid.import_limit_w == 2500  # the floor applies again
    assert len(full.steps) > len(july.steps)


# --- §14a ------------------------------------------------------------------------------------


def test_para14a_budget_holds_under_flicker() -> None:
    result, _ = run_named("para14a_dimming")
    budget = result.scenario.grid.para14a_limit_w if result.scenario.grid else 0
    settle = 30  # a write and its settle time after the signal starts
    for i, _ in enumerate(result.steps):
        t = i * result.step_s
        if 1800 + settle <= t < 5400:
            total = result.devices["rod"].power_w[i] + result.devices["charger"].power_w[i]
            assert total <= budget + 1.0, (t, total)
    dims = [
        d.intent.asset_id
        for d in result.accepted()
        if any(r.key == "controller.grid.para14a_dim" for r in d.intent.reasons)
    ]
    assert sorted(dims) == ["charger", "rod"]
    # Cloud flicker inside the window: the dimmed charger does not come back and go again.
    in_window = [i for i, _ in result.transitions("charger") if 1800 <= i * result.step_s < 5400]
    assert len(in_window) <= 1


def test_para14a_unknown_and_cooldown_block_increases() -> None:
    result, _ = run_named("para14a_dimming")
    cooldown = SETTINGS.safe_state_cooldown_s
    for device_id in ("rod", "charger"):
        for t in rises(result, device_id):
            assert not 1800 <= t < 5400 + cooldown, (device_id, t)  # dimmed, then cool-down
            assert not 7200 <= t < 7800 + cooldown, (device_id, t)  # unknown, then cool-down


def test_para14a_shadow_says_would_dim() -> None:
    result, _ = run_named("para14a_dimming", shadow=True)
    assert all(not on for on in result.devices["charger"].on[:5])
    shadow = [d for d in result.decisions if d.kind is DecisionKind.SHADOW]
    assert shadow
    assert any(
        r.key == "controller.grid.para14a_dim" for d in shadow for r in d.intent.reasons
    ) or any(r.key == "safety.para14a_limit" for d in shadow for r in d.reasons)
    assert result.accepted() == []


# --- island ------------------------------------------------------------------------------------


def test_island_sheds_keeps_and_raises_nothing() -> None:
    result, runner = run_named("grid_outage_island")
    start, end = 1800, 5400
    island = [i for i, s in enumerate(result.steps) if s.island]
    assert island[0] * result.step_s == start
    assert all(result.steps[i].grid_w == 0 for i in island)
    shed = [
        d
        for d in result.accepted("rod")
        if any(r.key == "controller.grid.island_shed" for r in d.intent.reasons)
    ]
    assert shed
    assert offset(result, shed[0].intent.ts) - start <= 30
    for i in island:
        if i * result.step_s >= start + 30:
            assert result.devices["rod"].power_w[i] == 0
    # The pump is on the keep list: it keeps running and is not touched.
    assert all(result.devices["pool"].on[i] for i in island)
    assert not any(start <= offset(result, d.intent.ts) < end for d in result.accepted("pool"))
    # Nothing is raised in island nor during the cool-down after it.
    cooldown = SETTINGS.safe_state_cooldown_s
    for device_id in ("rod", "pool"):
        for t in rises(result, device_id):
            assert not start <= t < end + cooldown, (device_id, t)
    assert rises(result, "rod")  # ... but afterwards the rod comes back on surplus
    assert runner.stack is not None
    assert runner.stack.grid is not None
    assert runner.stack.grid.grid_state is GridState.CONNECTED


def test_unknown_backup_state_counts_as_connected() -> None:
    result, _ = run_named("grid_outage_island")
    unknown = [d for d in result.accepted() if 9000 <= offset(result, d.intent.ts) < 9600]
    assert not any(
        r.key.startswith("controller.grid.island") for d in unknown for r in d.intent.reasons
    )
    i = at(result, 9300)
    assert result.devices["rod"].power_w[i] > 0  # not shed on an unavailable backup entity
