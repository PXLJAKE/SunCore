# SPDX-License-Identifier: Apache-2.0
"""The inverter limit verification procedure (plan phase 7 WP8, ADR-0018 addendum, PR 7-J).

``inverter.limit_verified`` lifts the inverter asset's cap at assisted (X6) and tells the gate
that an export limit really limits the export (X2 no longer reads it as an AC limit). This
procedure is the only way to set it. It is **not a writer**: every write is an intent that goes
through :class:`~suncore.safety.gate.SafetyGate` like any other (boundary test
``tests/app/test_verification_boundary.py``), and the only thing it gets from the gate beyond
that is a short :class:`~suncore.core.verification.VerificationWindow` - the single waiver of X3.

Steps (deterministic; time comes from the stack's clock, the stack calls :meth:`tick` after
each controller tick):

1. **checking** - the preconditions (:meth:`preconditions`) must hold for
   :data:`STABLE_S` without the export moving more than :data:`STABLE_RANGE_W`;
2. **ready** - the baseline (the user's own limit) is read and the test limit proposed:
   ``max(house + battery charging + 700 W, export - 1 kW)`` as W, or as the whole percent of
   ``nominal_ac_w`` that allows at least that (``ac_limit``). Nothing is written until the user
   confirms within :data:`CONFIRM_S`;
3. **writing** - :meth:`confirm` re-checks the preconditions, opens the window and submits the
   limit as an ``Origin.USER`` intent of that window; the gate judges it (X1, X2, X4-X6 and the
   waived X3), writes it and reads it back;
4. **observing** - within the adapter's settle time + 60 s the export must settle near the
   limit (export semantics) or near the limit minus the house load (AC semantics) for
   :data:`HOLD_S`; the observed semantics must match the configured capability;
5. **releasing** - the gate restores the baseline; the inverter must show it again and the
   export must recover; then **passed**.

Everything that goes wrong - a failed check, a timeout, the gate ending the lease (a rising
house load breaks X2), an emergency stop, a lost connection, a configuration change, the user
closing the dialog - releases through the gate at once and closes the window; a crash is
covered by the gate's restart recovery (the window is never persisted).
"""

from __future__ import annotations

import math
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Final, Literal

import structlog

from suncore.core.battery import BATTERY_ASSET_ID
from suncore.core.config import InverterVerificationRecord
from suncore.core.decisions import (
    Decision,
    DecisionKind,
    Intent,
    InverterLimitAction,
    OperatingMode,
    Origin,
    Reason,
)
from suncore.core.grid import GridState
from suncore.core.inverter import INVERTER_ASSET_ID, pct_for_ac_w, same_limit
from suncore.core.signals import Quality
from suncore.core.verification import (
    MAX_WINDOW_S,
    VerificationWindow,
    verification_intent_id,
)
from suncore.core.verification_view import TERMINAL_PHASES, VerificationPhase, VerificationView

if TYPE_CHECKING:
    from suncore.app.control import ControlStack

log = structlog.get_logger(__name__)

Semantics = Literal["export_limit", "ac_limit"]

MIN_EXPORT_W: Final = 1_000.0
"""Precondition: at least this much export."""
EXPORT_ABOVE_LOAD_W: Final = 1_500.0
"""Precondition: export at least house load + battery charging + this."""
TEST_MARGIN_W: Final = 700.0
"""The test limit leaves house load + battery charging + this (above X2's 500 W)."""
TEST_STEP_W: Final = 1_000.0
"""The test limit is at most this far below the export (``export - 1 kW``)."""
STABLE_S: Final = 120.0
"""The preconditions must hold this long before the test limit is proposed."""
STABLE_RANGE_W: Final = 1_000.0
"""... with the export moving by at most this much (cloud flicker restarts the wait)."""
CHECK_TIMEOUT_S: Final = 900.0
"""Without a stable phase within this time the procedure gives up."""
CONFIRM_S: Final = 120.0
"""The proposed test limit is valid this long; confirming later starts over."""
FOLLOW_EXTRA_S: Final = 60.0
"""The export must follow within the adapter's settle time + this (and recover likewise)."""
HOLD_S: Final = 20.0
"""The export must stay near one prediction this long to classify the semantics."""
TOLERANCE_W: Final = 150.0
TOLERANCE_SHARE: Final = 0.05
"""Classification tolerance: ``max(150 W, 5 % of the limit)``."""
RECOVER_W: Final = 200.0
"""After the release the export must rise at least this far above the test export."""

REASON_RUNNING: Final = Reason(key="verify.running")
"""The export chain sends nothing while the procedure runs (``ControlStack``)."""
REASON_RELEASED: Final = Reason(key="verify.released")

EVIDENCE: Final = frozenset(
    {
        "verify.semantics_mismatch",
        "verify.export_not_following",
        "verify.baseline_not_restored",
        "safety.readback_mismatch",
    }
)
"""Failures that are evidence against the adapter: they reset ``limit_verified``."""

FinishedCallback = Callable[[InverterVerificationRecord, bool | None], None]
"""``(record, verified)`` - ``True`` sets ``limit_verified``, ``False`` resets it, ``None``
keeps it. Called synchronously; the owner persists it in a task of its own (the call may come
from inside the stack's tick)."""


@dataclass(frozen=True, slots=True)
class _Live:
    export_w: float
    house_w: float
    battery_w: float

    @property
    def charging_w(self) -> float:
        return max(0.0, self.battery_w)


def test_limit_w(live_export_w: float, house_w: float, battery_w: float) -> float:
    """The test limit in W: ``max(house + battery charging + 700 W, export - 1 kW)``."""
    return max(house_w + max(0.0, battery_w) + TEST_MARGIN_W, live_export_w - TEST_STEP_W)


def tolerance_w(limit_w: float) -> float:
    return max(TOLERANCE_W, TOLERANCE_SHARE * limit_w)


def classify(
    export_w: float, limit_w: float, house_w: float, battery_w: float
) -> Semantics | Literal["unclear"] | None:
    """What the observed export says about the written limit (``limit_w`` in W).

    Export semantics: the export settles at the limit. AC semantics: at the limit minus the
    house load (a DC-coupled battery charges before the AC output) or minus house and battery
    charging (an AC-coupled one). ``unclear`` = near both; ``None`` = near neither (yet).
    """
    tol = tolerance_w(limit_w)
    near_export = abs(export_w - limit_w) <= tol
    ac_candidates = (limit_w - house_w, limit_w - house_w - max(0.0, battery_w))
    near_ac = min(abs(export_w - c) for c in ac_candidates) <= tol
    if near_export and near_ac:
        return "unclear"
    if near_export:
        return "export_limit"
    if near_ac:
        return "ac_limit"
    return None


class InverterVerification:
    """One run of the verification test on one control stack (see the module docstring)."""

    def __init__(
        self,
        stack: ControlStack,
        *,
        on_finished: FinishedCallback | None = None,
        last: InverterVerificationRecord | None = None,
        window_s: float | None = None,
    ) -> None:
        """``window_s``: the window's length instead of the computed one (tests; never more
        than :data:`~suncore.core.verification.MAX_WINDOW_S`)."""
        self._stack = stack
        self._window_s = window_s
        self._on_finished = on_finished
        self._last = last
        self.phase = VerificationPhase.IDLE
        self.reasons: tuple[Reason, ...] = ()
        self._started: datetime | None = None
        self._stable_since: datetime | None = None
        self._range: tuple[float, float] | None = None
        self._ready_at: datetime | None = None
        self._baseline: InverterLimitAction | None = None
        self._test: InverterLimitAction | None = None
        self._test_w: float | None = None
        self._export_before: float | None = None
        self._export_during: float | None = None
        self._window: VerificationWindow | None = None
        self._written_at: datetime | None = None
        self._match: tuple[str, datetime] | None = None
        self._unclear = False
        self._semantics: Semantics | None = None
        self._release_at: datetime | None = None
        self._outcome: VerificationPhase | None = None
        self._outcome_reason: Reason | None = None
        self._cause: Reason | None = None
        """What the gate said when it ended the lease (shown after the failure reason)."""
        self._busy = False
        self._abort_requested = False
        self._live: _Live | None = None

    # --- views -----------------------------------------------------------------------------

    @property
    def active(self) -> bool:
        """The procedure runs (the export chain stays out of the way)."""
        return self.phase not in TERMINAL_PHASES

    @property
    def window(self) -> VerificationWindow | None:
        return self._window

    def view(self) -> VerificationView:
        stack = self._stack
        wiring = stack.inverter_wiring
        now = stack.now()
        stable = (
            (now - self._stable_since).total_seconds()
            if self._stable_since is not None and self.phase is VerificationPhase.CHECKING
            else (STABLE_S if self.phase is not VerificationPhase.IDLE else 0.0)
        )
        live = self._live
        return VerificationView(
            phase=self.phase,
            adapter=stack.inverter_driver is not None and wiring is not None,
            capability=None if stack.inverter_driver is None else stack.inverter_driver.capability,
            verified=wiring is not None and wiring.config.verified,
            reasons=list(self.reasons),
            stable_s=round(min(stable, STABLE_S), 1),
            stable_needed_s=STABLE_S,
            baseline=self._baseline,
            test_limit=self._test,
            test_limit_w=None if self._test_w is None else round(self._test_w),
            confirm_by=(
                self._ready_at + timedelta(seconds=CONFIRM_S)
                if self._ready_at is not None and self.phase is VerificationPhase.READY
                else None
            ),
            window_expires_at=None if self._window is None else self._window.expires_at,
            export_w=None if live is None else round(live.export_w),
            house_w=None if live is None else round(live.house_w),
            battery_w=None if live is None else round(live.battery_w),
            export_before_w=None if self._export_before is None else round(self._export_before),
            export_during_w=None if self._export_during is None else round(self._export_during),
            semantics=self._semantics,
            last=self._last,
        )

    # --- inputs ----------------------------------------------------------------------------

    def _read_live(self, now: datetime) -> _Live | None:
        """Grid, house and battery from the controller's latest inputs: fresh and ``OK`` only."""
        stack = self._stack
        inputs = stack.loop.gate_inputs
        settings = stack.settings
        max_age = timedelta(seconds=settings.inputs_max_age_ticks * settings.tick_s)
        flows = None if inputs is None else inputs.flows
        if inputs is None or flows is None or inputs.ts is None or now - inputs.ts > max_age:
            return None
        values = []
        for flow in (flows.grid_w, flows.house_w, flows.battery_w):
            if flow.quality is not Quality.OK or flow.value is None:
                return None
            values.append(flow.value)
        grid, house, battery = values
        return _Live(export_w=max(0.0, -grid), house_w=house, battery_w=battery)

    def preconditions(self, now: datetime) -> tuple[Reason, ...]:
        """Everything that forbids the test now (empty = it may run). ``verify.export_low`` is
        the only one :meth:`tick` waits out; the others end a running procedure."""
        stack = self._stack
        gate = stack.gate
        driver, wiring = stack.inverter_driver, stack.inverter_wiring
        if driver is None or wiring is None:
            return (Reason(key="verify.no_adapter"),)
        reasons: list[Reason] = []
        if gate.emergency_stopped:
            reasons.append(Reason(key="safety.emergency_stop"))
        if stack.lost:
            reasons.append(Reason(key="verify.connection_lost"))
        if gate.modes_of(INVERTER_ASSET_ID).effective is not OperatingMode.ASSISTED:
            reasons.append(Reason(key="verify.not_assisted"))
        if not driver.readable() or stack.inverter_limit is None:
            reasons.append(Reason(key="verify.limit_unreadable"))
        pause = stack.watchdog.pause_reason(INVERTER_ASSET_ID, now)
        if pause is not None:
            reasons.append(pause)
        inputs = stack.loop.gate_inputs
        if inputs is not None and inputs.grid_state is GridState.ISLAND:
            reasons.append(Reason(key="verify.island"))
        battery = gate.runtime_of(BATTERY_ASSET_ID)
        if battery is not None and battery.under_control:
            reasons.append(Reason(key="verify.battery_held"))
        inverter = gate.runtime_of(INVERTER_ASSET_ID)
        before_write = self.phase in (
            VerificationPhase.IDLE,
            VerificationPhase.CHECKING,
            VerificationPhase.READY,
        )
        if (
            before_write
            and inverter is not None
            and (inverter.under_control or inverter.release_pending or inverter.is_held(now))
        ):
            reasons.append(Reason(key="verify.inverter_busy"))
        live = self._read_live(now)
        self._live = live
        if live is None:
            reasons.append(Reason(key="verify.inputs_unknown"))
        elif not reasons:
            needed = max(MIN_EXPORT_W, live.house_w + live.charging_w + EXPORT_ABOVE_LOAD_W)
            if live.export_w < needed:
                reasons.append(
                    Reason(
                        key="verify.export_low",
                        args={"export_w": round(live.export_w), "needed_w": round(needed)},
                    )
                )
        return tuple(reasons)

    # --- the user's commands ---------------------------------------------------------------

    def start(self) -> tuple[Reason, ...]:
        """Start the test; returns the reasons it cannot start (empty = started). One object
        runs one test: the owner makes a new one for the next."""
        if self.phase is not VerificationPhase.IDLE:
            return (Reason(key="verify.already_running"),)
        now = self._stack.now()
        reasons = self.preconditions(now)
        if reasons:
            self.reasons = reasons
            return reasons
        self.phase = VerificationPhase.CHECKING
        self._started = now
        self._stable_since = now
        assert self._live is not None
        self._range = (self._live.export_w, self._live.export_w)
        self.reasons = ()
        log.info("verify.started")
        return ()

    async def confirm(self) -> Decision | None:  # noqa: PLR0911 - one return per verdict
        """The user confirmed the proposed test limit: open the window and submit it.

        Returns the gate's decision (``None`` = not in the ``ready`` phase, nothing submitted).
        """
        if self.phase is not VerificationPhase.READY or self._busy:
            return None
        stack = self._stack
        gate = stack.gate
        now = stack.now()
        assert self._ready_at is not None
        if now - self._ready_at > timedelta(seconds=CONFIRM_S):
            self._finish(VerificationPhase.FAILED, Reason(key="verify.confirm_timeout"))
            return None
        reasons = self.preconditions(now)
        if reasons:
            self._finish(VerificationPhase.FAILED, reasons[0])
            return None
        live = self._live
        driver = stack.inverter_driver
        assert live is not None
        assert driver is not None
        limit, limit_w = self._test_limit(live)
        if limit is None or limit_w is None:
            self._finish(VerificationPhase.FAILED, Reason(key="verify.limit_out_of_range"))
            return None
        self._test, self._test_w = limit, limit_w
        self._export_before = live.export_w
        budget = min(
            MAX_WINDOW_S,
            self._window_s or 3 * driver.settle_s + 2 * FOLLOW_EXTRA_S + HOLD_S + 120.0,
        )
        window = VerificationWindow(
            id=f"v{secrets.token_hex(6)}",
            opened_at=now,
            expires_at=now + timedelta(seconds=budget),
            lowest=limit,
        )
        refused = gate.open_verification_window(window)
        if refused is not None:
            self._finish(VerificationPhase.FAILED, refused)
            return None
        self._window = window
        self.phase = VerificationPhase.WRITING
        self._busy = True
        intent = Intent(
            id=verification_intent_id(window.id, "test"),
            ts=now,
            asset_id=INVERTER_ASSET_ID,
            action=limit,
            reasons=(Reason(key="verify.test_limit", args={"limit_w": round(limit_w)}),),
            origin=Origin.USER,
        )
        try:
            decision = await gate.submit(intent)
        except Exception:
            log.exception("verify.submit_failed")
            self._busy = False
            await self._fail(Reason(key="safety.write_failed"))
            return None
        self._busy = False
        if self._abort_requested:
            await self._abort_now(Reason(key="verify.aborted"))
            return decision
        runtime = gate.runtime_of(INVERTER_ASSET_ID)
        if runtime is not None and runtime.under_control and runtime.baseline is not None:
            # What the gate restores is what the user had at the takeover.
            taken = runtime.baseline
            self._baseline = taken if isinstance(taken, InverterLimitAction) else self._baseline
        if decision.kind is DecisionKind.ACCEPTED:
            self.phase = VerificationPhase.OBSERVING
            self._written_at = now
            self._match = None
            log.info("verify.test_limit_written", limit=limit.model_dump(exclude_none=True))
        else:
            reason = decision.reasons[0] if decision.reasons else Reason(key="safety.write_failed")
            await self._fail(reason)
        return decision

    async def abort(self, reason: Reason | None = None) -> None:
        """The user closed the dialog or pressed cancel: release at once (idempotent)."""
        why = reason or Reason(key="verify.aborted")
        if not self.active:
            return
        if self._busy:
            self._abort_requested = True  # the running confirm releases when the gate answers
            return
        await self._abort_now(why)

    # --- lifecycle hooks of the stack ------------------------------------------------------

    def connection_lost(self) -> None:
        """Nothing can be written now: the window ends (the gate already dropped it) and the
        gate releases a held test limit when the link is back (``connection_restored``)."""
        if self.active:
            self._close_window()
            self._finish(VerificationPhase.FAILED, Reason(key="verify.connection_lost"))

    def stopped(self, reason: Reason) -> None:
        """The stack stops (shutdown, configuration change): its gate releases everything."""
        if self.active:
            self._close_window()
            self._finish(VerificationPhase.ABORTED, reason)

    # --- the tick --------------------------------------------------------------------------

    async def tick(self, now: datetime) -> None:
        """Advance the procedure (the stack calls it after every controller tick)."""
        if not self.active or self._busy:
            return
        gate = self._stack.gate
        if gate.emergency_stopped:
            # The gate already released everything and dropped the window.
            self._close_window()
            self._finish(VerificationPhase.ABORTED, Reason(key="safety.emergency_stop"))
            return
        if self.phase is VerificationPhase.CHECKING:
            self._tick_checking(now)
        elif self.phase is VerificationPhase.READY:
            await self._tick_ready(now)
        elif self.phase is VerificationPhase.OBSERVING:
            await self._tick_observing(now)
        elif self.phase is VerificationPhase.RELEASING:
            self._tick_releasing(now)

    def _tick_checking(self, now: datetime) -> None:
        reasons = self.preconditions(now)
        assert self._started is not None
        hard = [r for r in reasons if r.key != "verify.export_low"]
        if hard:
            self._finish(VerificationPhase.FAILED, hard[0])
            return
        live = self._live
        if reasons or live is None:
            self._stable_since, self._range = None, None
            self.reasons = reasons
        else:
            low, high = self._range or (live.export_w, live.export_w)
            low, high = min(low, live.export_w), max(high, live.export_w)
            if self._stable_since is None or high - low > STABLE_RANGE_W:
                self._stable_since, (low, high) = now, (live.export_w, live.export_w)
            self._range = (low, high)
            self.reasons = ()
            if (now - self._stable_since).total_seconds() >= STABLE_S:
                self._propose(now, live)
                return
        if (now - self._started).total_seconds() > CHECK_TIMEOUT_S:
            self._finish(VerificationPhase.FAILED, Reason(key="verify.not_stable"))

    def _propose(self, now: datetime, live: _Live) -> None:
        limit, limit_w = self._test_limit(live)
        if limit is None or limit_w is None:
            self._finish(VerificationPhase.FAILED, Reason(key="verify.limit_out_of_range"))
            return
        self._baseline = self._stack.inverter_limit
        self._test, self._test_w = limit, limit_w
        self._export_before = live.export_w
        self._ready_at = now
        self.phase = VerificationPhase.READY
        log.info("verify.ready", limit=limit.model_dump(exclude_none=True))

    async def _tick_ready(self, now: datetime) -> None:
        assert self._ready_at is not None
        if now - self._ready_at > timedelta(seconds=CONFIRM_S):
            self._finish(VerificationPhase.FAILED, Reason(key="verify.confirm_timeout"))
            return
        hard = [r for r in self.preconditions(now) if r.key != "verify.export_low"]
        if hard:
            self._finish(VerificationPhase.FAILED, hard[0])
        elif self._live is not None:
            # The dialog shows what a confirmation would write now.
            limit, limit_w = self._test_limit(self._live)
            if limit is not None:
                self._test, self._test_w = limit, limit_w

    async def _tick_observing(self, now: datetime) -> None:
        gate = self._stack.gate
        if gate.verification_window is None:
            # Expired (the gate releases a limit written under it at its next poll).
            await self._fail(Reason(key="verify.window_expired"))
            return
        runtime = gate.runtime_of(INVERTER_ASSET_ID)
        if runtime is None or not runtime.under_control or runtime.release_pending:
            # The gate ended the lease (X2: the house load rose; X5; the watchdog; an override).
            last = self._stack.last_decisions.get(INVERTER_ASSET_ID)
            reason = (
                last.reasons[-1]
                if last is not None and last.reasons
                else Reason(key="verify.lease_ended")
            )
            self._cause = reason
            await self._fail(Reason(key="verify.lease_ended"))
            return
        live = self._read_live(now)
        self._live = live
        if live is None:
            await self._fail(Reason(key="verify.inputs_unknown"))
            return
        assert self._written_at is not None
        assert self._test_w is not None
        driver = self._stack.inverter_driver
        assert driver is not None
        kind = classify(live.export_w, self._test_w, live.house_w, live.battery_w)
        self._unclear = self._unclear or kind == "unclear"
        if kind is None or kind == "unclear":
            self._match = None
        elif self._match is None or self._match[0] != kind:
            self._match = (kind, now)
        if self._match is not None and (now - self._match[1]).total_seconds() >= HOLD_S:
            observed: Semantics = "export_limit" if self._match[0] == "export_limit" else "ac_limit"
            self._semantics = observed
            self._export_during = live.export_w
            if observed != driver.capability:
                await self._fail(
                    Reason(
                        key="verify.semantics_mismatch",
                        args={"observed": observed, "configured": driver.capability},
                    )
                )
                return
            await self._release(None)
            return
        deadline = self._written_at + timedelta(seconds=driver.settle_s + FOLLOW_EXTRA_S)
        if now > deadline:
            key = "verify.semantics_unclear" if self._unclear else "verify.export_not_following"
            self._export_during = live.export_w
            await self._fail(Reason(key=key))

    def _tick_releasing(self, now: datetime) -> None:
        stack = self._stack
        runtime = stack.gate.runtime_of(INVERTER_ASSET_ID)
        driver = stack.inverter_driver
        assert self._release_at is not None
        assert driver is not None
        deadline = self._release_at + timedelta(seconds=2 * driver.settle_s + FOLLOW_EXTRA_S)
        if runtime is not None and runtime.under_control:
            if now > deadline:
                self._settle(Reason(key="verify.release_unconfirmed"))
            return
        if not same_limit(stack.inverter_limit, self._baseline):
            self._settle(Reason(key="verify.baseline_not_restored"))
            return
        if self._outcome is not VerificationPhase.PASSED:
            self._settle(None)
            return
        live = self._read_live(now)
        self._live = live
        during = self._export_during
        if live is not None and during is not None and live.export_w >= during + RECOVER_W:
            self._settle(None)
        elif now > deadline:
            self._settle(Reason(key="verify.export_not_recovered"))

    # --- endings ---------------------------------------------------------------------------

    async def _release(self, failure: Reason | None, *, aborted: bool = False) -> None:
        """Give the user's own limit back through the gate and close the window."""
        stack = self._stack
        if aborted:
            self._outcome = VerificationPhase.ABORTED
        else:
            self._outcome = VerificationPhase.FAILED if failure else VerificationPhase.PASSED
        self._outcome_reason = failure
        runtime = stack.gate.runtime_of(INVERTER_ASSET_ID)
        if runtime is not None and runtime.under_control and not runtime.release_pending:
            await stack.gate.release_device(INVERTER_ASSET_ID, REASON_RELEASED, origin=Origin.USER)
        self._close_window()
        self.phase = VerificationPhase.RELEASING
        self._release_at = stack.now()
        self.reasons = () if failure is None else (failure,)
        self._tick_releasing(self._release_at)

    def _settle(self, problem: Reason | None) -> None:
        """The release is done (or failed): the final phase."""
        outcome = self._outcome or VerificationPhase.FAILED
        reason = self._outcome_reason
        if problem is not None and (
            outcome is VerificationPhase.PASSED or problem.key == "verify.baseline_not_restored"
        ):
            # A limit that did not come back is a failure whatever happened before.
            outcome, reason = VerificationPhase.FAILED, problem
        elif problem is not None and self._cause is None:
            self._cause = problem  # shown after the first reason (e.g. release unconfirmed)
        self._finish(outcome, reason)

    async def _fail(self, reason: Reason) -> None:
        await self._release(reason)

    async def _abort_now(self, reason: Reason) -> None:
        runtime = self._stack.gate.runtime_of(INVERTER_ASSET_ID)
        if self.phase in (VerificationPhase.CHECKING, VerificationPhase.READY) and not (
            runtime is not None and runtime.under_control
        ):
            self._finish(VerificationPhase.ABORTED, reason)
            return
        await self._release(reason, aborted=True)

    def _close_window(self) -> None:
        if self._window is not None:
            self._stack.gate.close_verification_window(self._window.id)

    def _finish(self, outcome: VerificationPhase, reason: Reason | None) -> None:
        self._close_window()
        self.phase = outcome
        self.reasons = () if reason is None else (reason,)
        if reason is not None and self._cause is not None:
            self.reasons = (reason, self._cause)
        self._busy = False
        log.info(
            "verify.finished", outcome=outcome.value, reason=None if reason is None else reason.key
        )
        wiring = self._stack.inverter_wiring
        if outcome is VerificationPhase.ABORTED or wiring is None or self._window is None:
            return  # nothing was tested: nothing to store
        record = InverterVerificationRecord(
            outcome="passed" if outcome is VerificationPhase.PASSED else "failed",
            reason=None if reason is None else reason.key,
            semantics=self._semantics,
            identity=wiring.identity,
            at=self._stack.now(),
        )
        self._last = record
        verified: bool | None
        if outcome is VerificationPhase.PASSED:
            verified = True
        elif reason is not None and reason.key in EVIDENCE:
            verified = False
        else:
            verified = None
        if self._on_finished is not None:
            try:
                self._on_finished(record, verified)
            except Exception:
                log.exception("verify.store_failed")

    # --- helpers ---------------------------------------------------------------------------

    def _test_limit(self, live: _Live) -> tuple[InverterLimitAction | None, float | None]:
        """The test limit for the adapter's unit and range (``None`` = cannot be expressed)."""
        driver, wiring = self._stack.inverter_driver, self._stack.inverter_wiring
        assert driver is not None
        assert wiring is not None
        wanted_w = test_limit_w(live.export_w, live.house_w, live.battery_w)
        low, high = driver.limit_range()
        if driver.capability == "export_limit":
            value = float(math.ceil(wanted_w))
            if not low <= value <= high:
                return None, None
            return InverterLimitAction(max_export_w=value), value
        nominal = wiring.config.nominal_ac_w
        if nominal is None:
            return None, None
        pct = pct_for_ac_w(wanted_w, nominal)
        if not low <= pct <= high or pct >= 100.0:
            return None, None
        return InverterLimitAction(max_ac_pct=pct), pct / 100.0 * nominal


__all__ = [
    "CHECK_TIMEOUT_S",
    "CONFIRM_S",
    "EVIDENCE",
    "FOLLOW_EXTRA_S",
    "HOLD_S",
    "REASON_RUNNING",
    "STABLE_S",
    "InverterVerification",
    "classify",
    "test_limit_w",
]
