# SPDX-License-Identifier: Apache-2.0
"""Grid-balance validation of power estimates (BRIEF §4.5).

When a device switches, the house load (``pv + grid - battery``) changes by about the
device's power. The validator compares the *measured* change of the house power around
a switch with the *expected* change (the model's estimate) and keeps a widening factor:

* the change matches (within the larger of a noise floor, the model's own uncertainty
  and a relative tolerance): the estimate is confirmed, the factor decays toward 1;
* it does not match: the estimate was wrong or another load interfered; the factor grows
  (up to ``max_factor``) so the controller prices the extra doubt in.

The validator is pure and time-injected. Switches that overlap in time are discarded
(confounded), switches smaller than ``min_delta_w`` cannot be seen in the noise and are
not validated. Only the factor is persisted.
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final

HOUSE_HISTORY_S: Final = 1800.0
EXPIRE_S: Final = 300.0
"""A pending switch without enough house samples is dropped this long after its window."""


@dataclass(frozen=True, slots=True)
class SwitchCheck:
    """Outcome of validating one switch."""

    switch_ts: datetime
    expected_delta_w: float
    measured_delta_w: float
    tolerance_w: float

    @property
    def confirmed(self) -> bool:
        return abs(self.measured_delta_w - self.expected_delta_w) <= self.tolerance_w


@dataclass(frozen=True, slots=True)
class _Pending:
    ts: datetime
    expected_w: float
    uncertainty_w: float


class GridBalanceValidator:
    """Confirms or widens a device's power estimate from the house power around a switch."""

    def __init__(
        self,
        *,
        baseline_s: float = 60.0,
        settle_s: float = 30.0,
        window_s: float = 60.0,
        min_delta_w: float = 150.0,
        noise_w: float = 100.0,
        rel_tolerance: float = 0.35,
        max_factor: float = 4.0,
    ) -> None:
        self._baseline_s = baseline_s
        self._settle_s = settle_s
        self._window_s = window_s
        self._min_delta_w = min_delta_w
        self._noise_w = noise_w
        self._rel_tol = rel_tolerance
        self._max_factor = max_factor
        self._house: deque[tuple[datetime, float]] = deque()
        self._pending: _Pending | None = None
        self._factor = 1.0
        self.last_check: SwitchCheck | None = None

    @property
    def factor(self) -> float:
        """Multiplier (>= 1) for the model's uncertainty."""
        return self._factor

    def note_house(self, now: datetime, house_w: float) -> None:
        """Record a house power sample (call every tick; duplicates are harmless)."""
        if not math.isfinite(house_w):
            return
        if self._house and now <= self._house[-1][0]:
            return
        self._house.append((now, house_w))
        limit = now - timedelta(seconds=HOUSE_HISTORY_S)
        while self._house and self._house[0][0] < limit:
            self._house.popleft()

    def note_switch(self, now: datetime, expected_delta_w: float, uncertainty_w: float) -> None:
        """A device switched at ``now``; ``expected_delta_w`` is the signed expected change."""
        if not math.isfinite(expected_delta_w) or abs(expected_delta_w) < self._min_delta_w:
            self._pending = None  # too small to see (and it confounds the previous one)
            return
        # A second switch inside the open window confounds both: keep only the newest.
        self._pending = _Pending(now, expected_delta_w, max(0.0, uncertainty_w))

    def evaluate(self, now: datetime) -> SwitchCheck | None:
        """Complete the pending validation if its window has passed; update the factor."""
        pending = self._pending
        if pending is None:
            return None
        done_at = pending.ts + timedelta(seconds=self._settle_s + self._window_s)
        if now < done_at:
            return None
        before = self._mean(pending.ts - timedelta(seconds=self._baseline_s), pending.ts)
        after = self._mean(pending.ts + timedelta(seconds=self._settle_s), done_at)
        if before is None or after is None:
            if now >= done_at + timedelta(seconds=EXPIRE_S):
                self._pending = None
            return None
        self._pending = None
        tolerance = max(
            self._noise_w, pending.uncertainty_w, self._rel_tol * abs(pending.expected_w)
        )
        check = SwitchCheck(pending.ts, pending.expected_w, after - before, tolerance)
        self.last_check = check
        if check.confirmed:
            self._factor = max(1.0, self._factor * 0.9)
        else:
            self._factor = min(self._max_factor, self._factor * 1.5)
        return check

    def _mean(self, start: datetime, end: datetime) -> float | None:
        values = [w for ts, w in self._house if start <= ts <= end]
        if len(values) < 2:
            return None
        return sum(values) / len(values)

    def dump_state(self) -> dict[str, Any]:
        return {"factor": self._factor}

    def load_state(self, data: Mapping[str, Any]) -> None:
        factor = data.get("factor")
        if (
            isinstance(factor, int | float)
            and math.isfinite(factor)
            and 1.0 <= factor <= self._max_factor
        ):
            self._factor = float(factor)
