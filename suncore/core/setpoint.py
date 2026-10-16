# SPDX-License-Identifier: Apache-2.0
"""Set-point rounding to what a climate device really accepts.

Home Assistant core does not round or validate the step of ``climate.set_temperature`` (only
``min_temp``..``max_temp``), and ``lg_thinq`` *truncates* with ``int()`` when the device step
is >= 1 (research/device-integrations.md, "Climate write behaviour"). A set-point such as
22.9 would silently become 22. The driver therefore rounds itself - and the controller should
use the same function to compute its targets, so that the value it asks for is the value that
is written and the read-back matches.
"""

from __future__ import annotations

import math
from typing import Final

EPSILON: Final = 1e-9


def snap_setpoint(
    value: float,
    *,
    base: float,
    step: float,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    """Round ``value`` to the ``step`` grid, never further from ``base`` than ``value`` was.

    A shift is rounded *toward the user's base set-point* (a shift of 0.9 K on a 1 K grid
    becomes 0, not 1): rounding can never push the set-point past the comfort limit the caller
    checked. ``value == base`` is returned unchanged (restoring the user's own value).
    The result is clamped to ``[minimum, maximum]`` (the entity's ``min_temp``/``max_temp``).
    """
    if step <= 0 or not math.isfinite(step):
        msg = "step must be positive"
        raise ValueError(msg)
    if abs(value - base) <= EPSILON:
        snapped = base
    else:
        ratio = value / step
        grid = math.floor(ratio + EPSILON) if value >= base else math.ceil(ratio - EPSILON)
        candidate = grid * step
        low, high = min(base, value), max(base, value)
        # An off-grid base can leave no grid point between base and value: keep the base.
        snapped = candidate if low - EPSILON <= candidate <= high + EPSILON else base
    if minimum is not None:
        snapped = max(snapped, minimum)
    if maximum is not None:
        snapped = min(snapped, maximum)
    return round(snapped, 3)


__all__ = ["snap_setpoint"]
