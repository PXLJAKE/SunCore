# SPDX-License-Identifier: Apache-2.0
"""Read models of the planner runtime (Phase 4 WP9): what ``/api/v1/plan*`` and the live feed serve.

Pure data (``core`` only): the runtime (``suncore.app.planning``) fills them, the API serves them
and the OpenAPI document is generated from them.
"""

from __future__ import annotations

from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from suncore.core.control import Plan, PlanSource
from suncore.core.decisions import Reason

Trigger = Literal[
    "start", "schedule", "prices", "forecast", "deviation", "config", "override", "manual"
]
"""Why a run started: the first run, the 15-minute cadence, or an event."""
RunOutcome = Literal["ok", "failed", "skipped"]
"""``skipped``: nothing to plan with yet (or an earlier run still hangs); ``failed``: the run
raised or timed out - the last good plan stays."""


class _View(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class PlanView(_View):
    """``GET /api/v1/plan``: the current plan and whether the controller follows it.

    ``plan`` is :meth:`Plan.default` (no intervals, ``source = "default"``) until the first
    plan exists. ``in_use`` is ``False`` when the plan is stale or has run out: the controller
    then runs on Phase 3 rules only (``fallback`` says why, as a catalogue reason).
    """

    generated_at: AwareDatetime
    plan: Plan
    stale: bool
    """Older than the stale limit (2 h): shown, but not handed to the controller."""
    expired: bool
    """Every interval lies in the past."""
    in_use: bool
    fallback: Reason | None = None


class PlanSummary(_View):
    """The live feed's ``plan`` message and the status' short form: no intervals."""

    created_at: AwareDatetime | None
    source: PlanSource
    status: str | None
    """Solver status slug (``optimal``, ``time_limit``, ``heuristic`` ...)."""
    solve_time_s: float | None
    intervals: int
    horizon_end: AwareDatetime | None
    stale: bool
    in_use: bool
    reasons: list[Reason] = Field(default_factory=list)
    """Plan-level reasons (who made the plan, input gaps)."""


class PlanStatusView(_View):
    """``GET /api/v1/plan/status``: the scheduler's bookkeeping."""

    generated_at: AwareDatetime
    running: bool
    last_trigger: Trigger | None
    last_started_at: AwareDatetime | None
    last_finished_at: AwareDatetime | None
    last_duration_s: float | None
    last_outcome: RunOutcome | None
    last_ok_at: AwareDatetime | None
    consecutive_failures: int
    reason: Reason | None = None
    """Why the last run did not produce a plan (``outcome`` skipped or failed)."""
    next_scheduled_at: AwareDatetime | None
    """The next quarter-hour run (cadence)."""
    pending: list[Trigger] = Field(default_factory=list)
    """Events waiting for the debounce."""
    earliest_event_run_at: AwareDatetime | None
    """When a pending event may start a run (debounce)."""
    plan: PlanSummary


class ReplanAck(_View):
    """``POST /api/v1/plan/replan`` (202): the request is queued, not done."""

    accepted: bool = True
    earliest_start_at: AwareDatetime
    """Start of the run: now, or the end of the debounce interval."""


__all__ = [
    "PlanStatusView",
    "PlanSummary",
    "PlanView",
    "ReplanAck",
    "RunOutcome",
    "Trigger",
]
