# SPDX-License-Identifier: Apache-2.0
"""Usable length of each planning slot (Phase 7 WP9, ``docs/research/late-car-gap.md``).

The planning grid starts at the quarter hour that contains ``now``
(:func:`suncore.core.series.planning_grid`), so its first slot has partly passed when the plan
is made. Every energy the planner books in a slot is ``power * hours[t]``: the first slot
counts only the time that is left, ``starts[0] + step - now``, every later slot its full
``step``. Powers (bands, limits, flows) stay powers; only their energy and cost shrink.

A remainder below :data:`FIRST_SLOT_MIN` is counted as :data:`FIRST_SLOT_MIN` (at most one
second of full power booked too much): energy coefficients of order ``1e-10`` would fall under
the solver's matrix tolerance and leave the first slot's flows undetermined. The slot stays in
the plan (the controller needs an interval that covers ``now``); it just carries no energy.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Final

FIRST_SLOT_MIN: Final = timedelta(seconds=1)
"""Shortest usable remainder of the first slot (see the module doc)."""

HOUR: Final = timedelta(hours=1)


def first_slot_left(now: datetime, start: datetime, step: timedelta) -> timedelta:
    """Usable time of the slot ``[start, start + step)`` at ``now``, in ``[FIRST_SLOT_MIN,
    step]`` (all of it when ``now`` is at or before ``start``)."""
    left = start + step - now if now > start else step
    return min(step, max(FIRST_SLOT_MIN, left))


def slot_hours(now: datetime, starts: Sequence[datetime], step: timedelta) -> tuple[float, ...]:
    """Hours of each slot that the plan can still use (module doc)."""
    if not starts:
        return ()
    full = step / HOUR
    return (first_slot_left(now, starts[0], step) / HOUR,) + (full,) * (len(starts) - 1)


__all__ = ["FIRST_SLOT_MIN", "first_slot_left", "slot_hours"]
