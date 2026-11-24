# SPDX-License-Identifier: Apache-2.0
"""The island/§14a memory behind the cool-down (G6, PR 6-F)."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

from hypothesis import given, settings
from hypothesis import strategies as st

from suncore.core.grid import (
    GridMemory,
    GridState,
    Para14aStatus,
    advance_grid_memory,
    grid_memory_from_events,
    island_hold_s,
    merge_grid_memory,
    para14a_hold_s,
)

T0 = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)
COOLDOWN = 120.0


def walk(states: list[tuple[GridState, Para14aStatus]], step_s: float = 5.0) -> list[GridMemory]:
    memory, out = GridMemory(), []
    for i, (grid, para) in enumerate(states):
        memory = advance_grid_memory(memory, T0 + timedelta(seconds=i * step_s), grid, para)
        out.append(memory)
    return out


def test_island_then_unknown_then_connected() -> None:
    seq = [
        (GridState.CONNECTED, Para14aStatus.INACTIVE),
        (GridState.ISLAND, Para14aStatus.INACTIVE),
        (GridState.UNKNOWN, Para14aStatus.INACTIVE),
        (GridState.CONNECTED, Para14aStatus.INACTIVE),
    ]
    memories = walk(seq)
    assert island_hold_s(memories[0], T0, COOLDOWN) == 0
    assert math.isinf(island_hold_s(memories[1], T0, COOLDOWN))
    assert math.isinf(island_hold_s(memories[2], T0, COOLDOWN))  # unknown after island
    back = T0 + timedelta(seconds=15)
    assert island_hold_s(memories[3], back, COOLDOWN) == COOLDOWN
    assert island_hold_s(memories[3], back + timedelta(seconds=COOLDOWN), COOLDOWN) == 0


def test_para14a_cooldown_starts_when_inactive_again() -> None:
    seq = [
        (GridState.NOT_CONFIGURED, Para14aStatus.ACTIVE),
        (GridState.NOT_CONFIGURED, Para14aStatus.UNKNOWN),
        (GridState.NOT_CONFIGURED, Para14aStatus.INACTIVE),
    ]
    memories = walk(seq)
    assert para14a_hold_s(memories[0], T0, COOLDOWN) == 0  # budget rules apply, not the hold
    assert para14a_hold_s(memories[1], T0, COOLDOWN) == 0
    end = T0 + timedelta(seconds=10)
    assert para14a_hold_s(memories[2], end + timedelta(seconds=20), COOLDOWN) == 100


def test_older_snapshots_are_ignored() -> None:
    memory = advance_grid_memory(GridMemory(), T0, GridState.ISLAND, Para14aStatus.INACTIVE)
    again = advance_grid_memory(memory, T0, GridState.CONNECTED, Para14aStatus.INACTIVE)
    assert again is memory


def test_memory_from_events_and_merge() -> None:
    """Restart (review H1): stored events rebuild the hold; a seed never shortens one."""
    ended = T0 - timedelta(seconds=30)
    memory = grid_memory_from_events(
        [
            (T0 - timedelta(hours=1), "island_start"),
            (ended, "island_end"),
            (T0 - timedelta(seconds=10), "para14a_start"),
            (T0 - timedelta(seconds=5), "peak_new"),
        ]
    )
    assert memory == GridMemory(island_ended_at=ended, para14a_seen=True)
    assert island_hold_s(memory, T0, COOLDOWN) == 90
    open_island = grid_memory_from_events([(T0, "island_start")])
    assert math.isinf(island_hold_s(open_island, T0 + timedelta(days=1), COOLDOWN))
    assert grid_memory_from_events([]) == GridMemory()
    live = GridMemory(island_ended_at=T0 - timedelta(seconds=100), ts=T0)
    merged = merge_grid_memory(live, memory)
    assert merged.island_ended_at == ended  # the later end counts
    assert merged.para14a_seen
    assert merged.ts == T0
    assert merge_grid_memory(open_island, live).island_seen


@settings(max_examples=300, deadline=None)
@given(
    st.lists(
        st.tuples(st.sampled_from(list(GridState)), st.sampled_from(list(Para14aStatus))),
        min_size=1,
        max_size=40,
    )
)
def test_no_increase_without_a_full_cooldown_after_island(
    seq: list[tuple[GridState, Para14aStatus]],
) -> None:
    """Every moment within ``cooldown`` of an island observation holds increases."""
    memories = walk(seq)
    last_island: datetime | None = None
    for i, ((grid, _), memory) in enumerate(zip(seq, memories, strict=True)):
        now = T0 + timedelta(seconds=i * 5.0)
        if grid is GridState.ISLAND:
            last_island = now
        hold = island_hold_s(memory, now, COOLDOWN)
        if last_island is not None and (now - last_island).total_seconds() < COOLDOWN:
            assert hold > 0
        if last_island is None:
            assert hold == 0
