# SPDX-License-Identifier: Apache-2.0
"""Pure priority allocation (``allocate_levels``): hypothesis invariants and examples."""

from __future__ import annotations

from datetime import UTC, datetime, time

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from suncore.controller import reasons as rs
from suncore.controller.allocation import Candidate, allocate_levels, window_reason
from suncore.controller.levels import Level, LevelTable
from suncore.core.decisions import StepAction, SwitchAction
from suncore.core.devices import TimeWindow

UNC = 50.0


def table(*powers: float, ramped: bool = False) -> LevelTable:
    return LevelTable(
        tuple(Level(i, p, StepAction(step=i)) for i, p in enumerate(powers)), ramped=ramped
    )


ON_OFF = LevelTable(
    (Level(0, 0.0, SwitchAction(on=False)), Level(1, 1000.0, SwitchAction(on=True)))
)


def cand(
    device_id: str,
    t: LevelTable,
    level: int = 0,
    *,
    draw: float | None = None,
    lower: float | None = None,
    own: float = 0.0,
    hyst: float = 100.0,
    cap: int | None = None,
    hold: bool = False,
) -> Candidate:
    power = t.level(level).power_w
    return Candidate(
        device_id=device_id,
        table=t,
        level=level,
        draw_w=power if draw is None else draw,
        current_lower_w=power if lower is None else lower,
        own_uncertainty_w=own,
        hysteresis_w=hyst,
        max_level=t.top if cap is None else cap,
        hold=hold,
    )


# --- examples ----------------------------------------------------------------------------


def test_priority_order_gets_served_first() -> None:
    wanted = allocate_levels([cand("a", ON_OFF), cand("b", ON_OFF)], 1500.0, 0.0)
    assert [w.level for w in wanted] == [1, 0]
    assert wanted[0].need_w == 1000.0
    assert wanted[1].reason.key == rs.NO_SURPLUS


def test_rise_needs_hysteresis_and_uncertainty() -> None:
    assert allocate_levels([cand("a", ON_OFF)], 1099.0, 0.0)[0].level == 0
    assert allocate_levels([cand("a", ON_OFF)], 1100.0, 0.0)[0].level == 1
    assert allocate_levels([cand("a", ON_OFF)], 1100.0, 1.0)[0].level == 0
    assert allocate_levels([cand("a", ON_OFF, own=300.0)], 1300.0, 0.0)[0].level == 0


def test_a_running_device_falls_only_below_minus_hysteresis() -> None:
    running = cand("a", ON_OFF, 1)
    assert allocate_levels([running], 950.0, 500.0)[0].level == 1  # median, not lower bound
    assert allocate_levels([running], 900.0, 0.0)[0].level == 1
    fall = allocate_levels([running], 899.0, 0.0)[0]
    assert fall.level == 0
    assert fall.reason.key == rs.SURPLUS_GONE


def test_steps_jump_to_the_highest_affordable_and_fall_to_what_covers_the_shortfall() -> None:
    t = table(0, 1000, 2000, 3000)
    assert allocate_levels([cand("h", t)], 2200.0, 0.0)[0].level == 2
    assert allocate_levels([cand("h", t, 3)], 1500.0, 0.0)[0].level == 1


def test_ramped_kinds_move_one_level_at_a_time() -> None:
    t = table(0, 800, 800, 800, ramped=True)
    assert allocate_levels([cand("ac", t)], 5000.0, 0.0)[0].level == 1
    assert allocate_levels([cand("ac", t, 3)], -5000.0, 0.0)[0].level == 2


def test_cap_forces_the_level_down_without_delay() -> None:
    t = table(0, 1000, 3000)
    w = allocate_levels([cand("h", t, 2, cap=1)], 9000.0, 0.0)[0]
    assert (w.level, w.forced) == (1, True)
    assert w.reason.key == rs.PLAN_BLOCKED
    w = allocate_levels([cand("h", t, 0, cap=0)], 9000.0, 0.0)[0]
    assert w.level == 0


def test_a_held_device_keeps_its_level_and_reservation() -> None:
    wanted = allocate_levels([cand("a", ON_OFF, 1, hold=True), cand("b", ON_OFF)], 1500.0, 0.0)
    assert wanted[0].level == 1
    assert wanted[0].reason.key == rs.IN_FLIGHT
    assert wanted[1].level == 0  # only 500 W left


def test_a_waiting_higher_priority_keeps_its_share() -> None:
    """The lower device runs on power the higher one is about to get: it must fall."""
    wanted = allocate_levels([cand("a", ON_OFF), cand("b", ON_OFF, 1)], 1600.0, 0.0)
    assert [w.level for w in wanted] == [1, 0]


def test_need_mirrors_the_gate_lower_bound() -> None:
    c = Candidate(
        device_id="ac",
        table=table(0, 800, 800, ramped=True),
        level=1,
        draw_w=800.0,
        current_lower_w=800.0,
        own_uncertainty_w=0.0,
        hysteresis_w=100.0,
        max_level=2,
        gate_lower_w=100.0,
    )
    w = allocate_levels([c], 2000.0, 0.0)[0]
    assert w.level == 2
    assert w.need_w == 700.0  # what the gate's grid invariant will compare
    assert w.reserved_w == 800.0


# --- windows -----------------------------------------------------------------------------


MONDAY_NOON = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)


def test_windows() -> None:
    allow = TimeWindow(start=time(10), end=time(14))
    assert window_reason((allow,), MONDAY_NOON) is None
    assert window_reason((allow,), MONDAY_NOON.replace(hour=15)) == rs.plain(rs.OUTSIDE_WINDOW)
    block = TimeWindow(start=time(11), end=time(13), mode="block")
    assert window_reason((allow, block), MONDAY_NOON) == rs.plain(rs.QUIET_HOURS)
    weekend = TimeWindow(start=time(0), end=time(23, 59), days=(5, 6))
    assert window_reason((weekend,), MONDAY_NOON) == rs.plain(rs.OUTSIDE_WINDOW)
    assert window_reason((), MONDAY_NOON) is None


def test_windows_over_midnight_belong_to_the_start_day() -> None:
    night = TimeWindow(start=time(22), end=time(6), days=(0,), mode="block")  # Monday night
    assert window_reason((night,), MONDAY_NOON.replace(hour=23)) == rs.plain(rs.QUIET_HOURS)
    tuesday_early = datetime(2026, 10, 13, 5, 0, tzinfo=UTC)
    assert window_reason((night,), tuesday_early) == rs.plain(rs.QUIET_HOURS)
    monday_early = MONDAY_NOON.replace(hour=5)
    assert window_reason((night,), monday_early) is None


# --- hypothesis --------------------------------------------------------------------------


@st.composite
def tables(draw: st.DrawFn) -> LevelTable:
    steps = draw(st.lists(st.integers(min_value=50, max_value=3000), min_size=1, max_size=4))
    powers = [0.0]
    for s in steps:
        powers.append(powers[-1] + s)
    return table(*powers, ramped=draw(st.booleans()))


@st.composite
def candidates(draw: st.DrawFn, device_id: str) -> Candidate:
    t = draw(tables())
    level = draw(st.integers(min_value=0, max_value=t.top))
    power = t.level(level).power_w
    draw_w = draw(st.floats(min_value=0, max_value=power * 1.5 + 1))
    lower = draw(st.floats(min_value=0, max_value=draw_w))
    return cand(
        device_id,
        t,
        level,
        draw=draw_w,
        lower=lower,
        own=draw(st.floats(min_value=0, max_value=500)),
        hyst=draw(st.floats(min_value=0, max_value=300)),
        cap=draw(st.integers(min_value=0, max_value=t.top)),
        hold=draw(st.booleans()) and draw(st.booleans()),
    )


@st.composite
def scenarios(draw: st.DrawFn) -> tuple[list[Candidate], float, float]:
    n = draw(st.integers(min_value=1, max_value=5))
    cs = [draw(candidates(f"d{i}")) for i in range(n)]
    total = draw(st.floats(min_value=-5000, max_value=15000))
    unc = draw(st.floats(min_value=0, max_value=2000))
    return cs, total, unc


def _replay(cs: list[Candidate], total: float, unc: float) -> list[tuple[float, float]]:
    """(remaining before, headroom) per device, from the reservations."""
    wanted = allocate_levels(cs, total, unc)
    out = []
    remaining = total
    for c, w in zip(cs, wanted, strict=True):
        out.append((remaining, remaining - c.draw_w))
        remaining -= w.reserved_w
    return out


@settings(max_examples=400)
@given(scenarios())
def test_a_rise_always_fits_free_surplus_minus_uncertainty(
    scenario: tuple[list[Candidate], float, float],
) -> None:
    cs, total, unc = scenario
    wanted = allocate_levels(cs, total, unc)
    replay = _replay(cs, total, unc)
    rises_added = 0.0
    last_riser = -1
    falls = False
    for i, (c, w) in enumerate(zip(cs, wanted, strict=True)):
        if w.level < c.level:
            falls = True
        if w.level > c.level:
            assert not c.hold
            assert w.level <= c.max_level
            added = c.table.level(w.level).power_w - c.current_lower_w
            _, headroom = replay[i]
            # Per device: need + hysteresis <= headroom - site and own uncertainty.
            assert added + c.hysteresis_w <= headroom - unc - c.own_uncertainty_w + 1e-6
            assert w.need_w >= added - 1e-9
            rises_added += max(0.0, added)
            last_riser = i
    if last_riser >= 0 and not falls:
        # In total: what the risers add fits the free surplus left by the devices before
        # them and by themselves, minus the uncertainty.
        draws = sum(c.draw_w for c in cs[: last_riser + 1])
        assert rises_added <= total - draws - unc + 1e-6


@settings(max_examples=400)
@given(scenarios())
def test_priority_is_respected(scenario: tuple[list[Candidate], float, float]) -> None:
    """A device that could not rise further could not afford its next level - its share was
    computed before any lower-priority device took anything."""
    cs, total, unc = scenario
    wanted = allocate_levels(cs, total, unc)
    replay = _replay(cs, total, unc)
    for i, (c, w) in enumerate(zip(cs, wanted, strict=True)):
        if c.hold or w.level < c.level or w.level >= min(c.max_level, c.table.top):
            continue
        if c.table.ramped and w.level > c.level:
            continue  # one level per delay
        _, headroom = replay[i]
        nxt = c.table.level(w.level + 1).power_w - c.current_lower_w
        assert nxt + c.hysteresis_w > headroom - unc - c.own_uncertainty_w - 1e-6


@settings(max_examples=200)
@given(scenarios())
def test_wanted_levels_are_valid(scenario: tuple[list[Candidate], float, float]) -> None:
    cs, total, unc = scenario
    for c, w in zip(cs, allocate_levels(cs, total, unc), strict=True):
        assert 0 <= w.level <= c.table.top
        assert w.level <= max(c.level, c.max_level) or w.level == c.level
        assert w.reserved_w >= 0
        if c.level > c.max_level and not c.hold:
            assert w.level == c.max_level
            assert w.forced


@pytest.mark.parametrize("total", [-1e9, 0.0, 1e9])
def test_extreme_totals(total: float) -> None:
    wanted = allocate_levels([cand("a", ON_OFF, 1), cand("b", table(0, 10, 20))], total, 0.0)
    assert len(wanted) == 2
