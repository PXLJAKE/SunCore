# SPDX-License-Identifier: Apache-2.0
"""The notification service: observe, decide, deliver, and take a confirmation back.

Runs beside the control layer (one asyncio task, never on the controller's tick) and never
touches a device. The **confirm path** (assisted proposals, ADR-0016 decision 7):

1. ``subscribe_notification_actions`` is attempted when Home Assistant is first connected. Only
   a successful subscription makes the buttons *active*; without it no button is ever offered
   and no tap is ever honoured (``ActionsState.REFUSED``/``PENDING``).
2. A button carries ``SUNCORE_<VERB>_<token>`` (:mod:`suncore.notify.actions`): a random,
   single-use token bound to one proposal and its expiry. The token is the only authentication
   (the event itself proves nothing); it is consumed synchronously by the first tap.
3. A confirm is executed only if the proposal is still open, not expired and the device is still
   in ``assisted`` mode; then exactly one call of :meth:`ProposalPort.confirm_proposal` (the
   services layer: the same ``gate.confirm`` as the panel, ``Origin.USER``). It cannot raise a
   mode or clear the emergency stop: the port has no such method. A reject calls nothing; the
   proposal lapses by itself.
4. The user gets a short answer (confirmed, rejected, no longer current, refused).
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, tzinfo
from enum import StrEnum
from typing import Any, Final, Protocol

import structlog

from suncore.core.config import NotificationsConfig
from suncore.core.decisions import Reason
from suncore.ha.connection import (
    CommandError,
    ConnectionState,
    HAError,
    NotificationAction,
    StateListener,
    Unsubscribe,
)
from suncore.notify.actions import ActionTokens, Consumed, Refusal, Verb
from suncore.notify.model import POLICIES, Category, NotifyEvent, Verdict
from suncore.notify.ratelimit import NotifyLimiter
from suncore.notify.render import render_action_titles, render_reason
from suncore.notify.rules import Observation, RuleEngine
from suncore.notify.sink import Button, HaNotificationSink, NotifyPort

log = structlog.get_logger(__name__)

POLL_S: Final = 5.0
MAX_IN_FLIGHT: Final = 8
TEST_INTERVAL_S: Final = 5.0

REASON_OFFLINE: Final = "notify.reason.actions_offline"
REASON_REFUSED: Final = "notify.reason.actions_refused"
REASON_NO_TARGETS: Final = "notify.reason.no_targets"
REASON_ENTITY_ONLY: Final = "notify.reason.actions_entity_only"


class ActionsState(StrEnum):
    PENDING = "pending"
    """Not subscribed yet (Home Assistant not connected, or the attempt is still to come)."""
    ACTIVE = "active"
    REFUSED = "refused"
    """Home Assistant refused the subscription: plain notifications only (until a restart)."""


class ConfirmOutcome(StrEnum):
    CONFIRMED = "confirmed"
    GONE = "gone"
    REFUSED = "refused"


@dataclass(frozen=True, slots=True)
class ProposalView:
    intent_id: str
    device_name: str
    expires_at: datetime
    assisted: bool
    """The device's effective mode when the proposal was made was ``assisted``."""


class ProposalPort(Protocol):
    """What the confirm path may do: look at open proposals and confirm one. Nothing else."""

    def open_proposals(self) -> Sequence[ProposalView]: ...

    async def confirm_proposal(self, intent_id: str) -> ConfirmOutcome: ...


class ObservationSource(Protocol):
    def observe(self, now: datetime) -> Observation: ...

    def language(self) -> str: ...

    def timezone(self) -> tzinfo: ...


class NotifyConnection(NotifyPort, Protocol):
    @property
    def state(self) -> ConnectionState: ...

    def add_state_listener(self, listener: StateListener) -> Unsubscribe: ...

    async def subscribe_notification_actions(
        self, listener: Callable[[NotificationAction], None]
    ) -> Unsubscribe: ...


@dataclass(frozen=True, slots=True)
class NotifyStatus:
    actions: ActionsState
    reason_key: str | None
    """Why buttons or push do not work as configured (``notify.reason.*``)."""
    push_targets: int
    button_targets: int
    pushed_today: int
    open_buttons: int


@dataclass(frozen=True, slots=True)
class TestResult:
    sent: int
    failed: int
    reason_key: str | None


class NotificationService:
    def __init__(
        self,
        *,
        connection: NotifyConnection,
        source: ObservationSource,
        proposals: ProposalPort,
        load_config: Callable[[], Awaitable[NotificationsConfig | None]],
        clock: Callable[[], datetime],
        poll_s: float = POLL_S,
        tokens: ActionTokens | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._conn = connection
        self._source = source
        self._proposals = proposals
        self._load_config = load_config
        self._clock = clock
        self._poll_s = poll_s
        self._tokens = tokens if tokens is not None else ActionTokens()
        self._mono = monotonic
        self._sink = HaNotificationSink(connection)
        self._limiter = NotifyLimiter()
        self._engine = RuleEngine()
        self._config = NotificationsConfig()
        self._actions = ActionsState.PENDING
        self._unsub_actions: Unsubscribe | None = None
        self._unsub_state: Unsubscribe | None = None
        self._task: asyncio.Task[None] | None = None
        self._tasks: set[asyncio.Task[Any]] = set()
        self._persistent: set[str] = set()
        self._lock = asyncio.Lock()
        self._last_test = float("-inf")

    # --- lifecycle ------------------------------------------------------------------------

    async def start(self) -> None:
        await self.reload_config()
        self._unsub_state = self._conn.add_state_listener(self._on_state)
        if self._conn.state is ConnectionState.CONNECTED:
            self._spawn(self._subscribe())
        self._task = asyncio.create_task(self._run(), name="suncore-notify")

    async def stop(self) -> None:
        if self._unsub_state is not None:
            self._unsub_state()
            self._unsub_state = None
        for task in (self._task, *self._tasks):
            if task is not None:
                task.cancel()
        for task in (self._task, *self._tasks):
            if task is not None:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        self._task = None
        self._tasks.clear()
        if self._unsub_actions is not None:
            self._unsub_actions()
            self._unsub_actions = None
        self._tokens.clear()

    async def reload_config(self) -> None:
        stored = await self._load_config()
        self._config = stored if stored is not None else NotificationsConfig()
        self._sink.configure(self._config.targets)

    def reconfigure(self) -> None:
        """The ``notifications`` section changed (or an import): read it again."""
        self._spawn(self.reload_config())

    # --- status ---------------------------------------------------------------------------

    @property
    def status(self) -> NotifyStatus:
        targets = self._config.targets
        buttons = sum(1 for t in targets if t.kind == "service")
        reason: str | None = None
        if self._actions is ActionsState.REFUSED:
            reason = REASON_REFUSED
        elif not targets:
            reason = REASON_NO_TARGETS
        elif self._actions is ActionsState.PENDING:
            reason = REASON_OFFLINE
        elif buttons == 0:
            reason = REASON_ENTITY_ONLY
        return NotifyStatus(
            self._actions,
            reason,
            len(targets),
            buttons,
            self._limiter.pushed_today,
            self._tokens.open_count(),
        )

    # --- the event stream from other producers --------------------------------------------

    def post(self, event: NotifyEvent) -> None:
        """Deliver ``event`` soon (for producers outside the observation: MQTT refusals,
        grid, program and EV events of later PRs)."""
        self._spawn(self.submit(event))

    async def submit(self, event: NotifyEvent) -> None:
        async with self._lock:
            await self._deliver(event)

    # --- the observation loop ---------------------------------------------------------------

    async def _run(self) -> None:
        while True:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("notify.tick_failed")
            await asyncio.sleep(self._poll_s)

    async def _tick(self) -> None:
        now = self._clock()
        evaluation = self._engine.evaluate(self._source.observe(now))
        self._tokens.prune(now)
        async with self._lock:
            for key in evaluation.resolved:
                if key in self._persistent:
                    self._persistent.discard(key)
                    await self._sink.persistent_dismiss(key)
            for event in evaluation.events:
                await self._deliver(event)

    # --- delivery ---------------------------------------------------------------------------

    async def _deliver(self, event: NotifyEvent) -> None:
        config = self._config
        if not getattr(config.categories, event.category.value, False):
            return
        now = self._clock()
        tz = self._source.timezone()
        if self._limiter.is_duplicate(event, now):
            return
        policy = POLICIES[event.category]
        persist = policy.persistent if event.persist is None else event.persist
        language = self._source.language()
        delivered = False
        if persist and config.persistent:
            text = render_reason(event.reason, language)
            if await self._sink.persistent_create(event.key, text.title, text.message):
                self._persistent.add(event.key)
                delivered = True
        verdict = self._limiter.judge_push(event, config, now, tz)
        pushed = False
        if verdict is Verdict.SEND:
            pushed = await self._push(event, language)
        elif verdict is not Verdict.DISABLED:
            log.info("notify.suppressed", key=event.key, verdict=verdict.value)
        if delivered or pushed:
            self._limiter.commit(event, now, tz, pushed=pushed)

    async def _push(self, event: NotifyEvent, language: str) -> bool:
        config = self._config
        buttons: list[Button] = []
        issued = False
        if (
            event.proposal is not None
            and self._actions is ActionsState.ACTIVE
            and any(t.kind == "service" for t in config.targets)
        ):
            ids = self._tokens.issue(event.proposal.intent_id, event.proposal.expires_at)
            confirm_title, reject_title = render_action_titles(language)
            buttons = [
                Button(ids[Verb.CONFIRM], confirm_title),
                Button(ids[Verb.REJECT], reject_title),
            ]
            issued = True
        plain = event.proposal is not None
        text = render_reason(event.reason, language, panel_hint=plain and not buttons)
        hinted = render_reason(event.reason, language, panel_hint=plain)
        report = await self._sink.push(
            config.targets,
            text.title,
            text.message,
            buttons=buttons,
            plain_message=hinted.message,
        )
        if issued and event.proposal is not None and report.with_buttons == 0:
            self._tokens.revoke(event.proposal.intent_id)  # nobody got the buttons
        return report.sent > 0

    async def send_test(self) -> TestResult:
        """A test message to every target (bypasses the category and the quiet hours; the daily
        cap does not count it). At most one per :data:`TEST_INTERVAL_S`."""
        if not self._config.targets:
            return TestResult(0, 0, REASON_NO_TARGETS)
        if self._mono() - self._last_test < TEST_INTERVAL_S:
            return TestResult(0, 0, "notify.reason.test_rate_limited")
        self._last_test = self._mono()
        text = render_reason(Reason(key="notify.test"), self._source.language())
        report = await self._sink.push(self._config.targets, text.title, text.message)
        return TestResult(report.sent, report.failed, None)

    # --- actions ---------------------------------------------------------------------------

    def _on_state(self, state: ConnectionState) -> None:
        if state is ConnectionState.CONNECTED and self._actions is ActionsState.PENDING:
            self._spawn(self._subscribe())

    async def _subscribe(self) -> None:
        if self._actions is not ActionsState.PENDING or self._unsub_actions is not None:
            return
        try:
            self._unsub_actions = await self._conn.subscribe_notification_actions(self._on_action)
        except CommandError as exc:
            self._actions = ActionsState.REFUSED
            log.warning("notify.actions_refused", code=exc.code)
        except (HAError, TimeoutError) as exc:
            log.info("notify.actions_subscribe_deferred", error=type(exc).__name__)
        else:
            self._actions = ActionsState.ACTIVE
            log.info("notify.actions_active")

    def _on_action(self, action: NotificationAction) -> None:
        """A tap (called from the connection's reader; never blocks, never raises)."""
        if self._actions is not ActionsState.ACTIVE:
            return  # cannot be authenticated: nothing is honoured
        outcome = self._tokens.consume(action.action, self._clock())
        if isinstance(outcome, Refusal):
            if outcome is not Refusal.NOT_OURS:
                log.warning("notify.action_refused", reason=outcome.value)
            return
        if len(self._tasks) >= MAX_IN_FLIGHT:
            log.warning("notify.action_dropped", reason="busy")
            return
        self._spawn(self._handle(outcome))

    async def _handle(self, tap: Consumed) -> None:
        now = self._clock()
        view = next(
            (p for p in self._proposals.open_proposals() if p.intent_id == tap.intent_id), None
        )
        name = "" if view is None else view.device_name
        if tap.verb is Verb.REJECT:
            outcome = "rejected"
        elif view is None or now >= view.expires_at or not view.assisted:
            outcome = "gone"
        else:
            try:
                result = await self._proposals.confirm_proposal(tap.intent_id)
            except Exception:
                log.exception("notify.confirm_failed")
                result = ConfirmOutcome.REFUSED
            outcome = result.value
        log.info("notify.action", verb=tap.verb.value, outcome=outcome)
        await self.submit(
            NotifyEvent(
                Category.PROPOSAL,
                f"proposal_result:{tap.intent_id}",
                Reason(key=f"notify.proposal.{outcome}", args={"device": name}),
                now,
                quiet_hours=False,
            )
        )

    def _spawn(self, coro: Awaitable[Any]) -> None:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._done)

    def _done(self, task: asyncio.Future[Any]) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            log.error("notify.task_failed", error=type(task.exception()).__name__)


__all__ = [
    "ActionsState",
    "ConfirmOutcome",
    "NotificationService",
    "NotifyStatus",
    "ProposalPort",
    "ProposalView",
    "TestResult",
]
