# SPDX-License-Identifier: Apache-2.0
"""The notification service: delivery, degradation and the confirm path (control-safety)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Collection, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, tzinfo
from typing import Any

import pytest

from suncore.core.config import (
    NotificationCategories,
    NotificationsConfig,
    NotifyTarget,
    QuietHours,
)
from suncore.core.decisions import Reason
from suncore.ha.connection import (
    CommandError,
    ConnectionState,
    HAError,
    NotConnectedError,
    NotificationAction,
    StateListener,
    Unsubscribe,
)
from suncore.notify import (
    ActionsState,
    Category,
    ConfirmOutcome,
    NotificationService,
    NotifyEvent,
    Observation,
    ProposalObs,
    ProposalView,
)
from suncore.notify.actions import Verb

T0 = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)
SERVICE = NotifyTarget(kind="service", id="mobile_app_phone")
ENTITY = NotifyTarget(kind="entity", id="notify.kitchen_display")


@dataclass
class StubConnection:
    state: ConnectionState = ConnectionState.CONNECTED
    sent: list[tuple[str, str, Mapping[str, Any], Mapping[str, Any] | None]] = field(
        default_factory=list
    )
    services: Collection[str] = ()
    subscribe_error: Exception | None = None
    notify_error: Exception | None = None
    listener: Callable[[NotificationAction], None] | None = None
    state_listeners: list[StateListener] = field(default_factory=list)

    async def notify(
        self,
        domain: str,
        service: str,
        data: Mapping[str, Any],
        target: Mapping[str, Any] | None = None,
    ) -> None:
        if self.notify_error is not None:
            raise self.notify_error
        self.sent.append((domain, service, data, target))

    def set_notify_services(self, services: Collection[str]) -> None:
        self.services = services

    def add_state_listener(self, listener: StateListener) -> Unsubscribe:
        self.state_listeners.append(listener)
        return lambda: self.state_listeners.remove(listener)

    async def subscribe_notification_actions(
        self, listener: Callable[[NotificationAction], None]
    ) -> Unsubscribe:
        if self.subscribe_error is not None:
            raise self.subscribe_error
        self.listener = listener
        return lambda: None

    def tap(self, action: str) -> None:
        assert self.listener is not None
        self.listener(NotificationAction(action))

    def pushes(self, service: str | None = None) -> list[Mapping[str, Any]]:
        return [
            d
            for dom, svc, d, _ in self.sent
            if dom == "notify" and (service is None or svc == service)
        ]

    def persistents(self, kind: str) -> list[Mapping[str, Any]]:
        return [
            d for dom, svc, d, _ in self.sent if dom == "persistent_notification" and svc == kind
        ]


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


class Source:
    def __init__(self, clock: Clock) -> None:
        self.clock = clock
        self.proposals: list[ProposalObs] = []
        self.emergency = False
        self.planner_failing_s: float | None = None

    def observe(self, now: datetime) -> Observation:
        return Observation(
            now=now,
            emergency_stop=self.emergency,
            global_mode="assisted",
            proposals=self.proposals,
            planner_failing_s=self.planner_failing_s,
        )

    def language(self) -> str:
        return "en"

    def timezone(self) -> tzinfo:
        return UTC


class Proposals:
    def __init__(self) -> None:
        self.views: list[ProposalView] = []
        self.confirmed: list[str] = []
        self.outcome = ConfirmOutcome.CONFIRMED
        self.error: Exception | None = None

    def open_proposals(self) -> list[ProposalView]:
        return list(self.views)

    async def confirm_proposal(self, intent_id: str) -> ConfirmOutcome:
        self.confirmed.append(intent_id)
        if self.error is not None:
            raise self.error
        return self.outcome


@dataclass
class Rig:
    service: NotificationService
    conn: StubConnection
    clock: Clock
    source: Source
    port: Proposals
    config: list[NotificationsConfig | None]

    async def tick(self) -> None:
        await self.service._tick()
        await self.drain()

    async def drain(self) -> None:
        for _ in range(10):
            tasks = list(self.service._tasks)
            if not tasks:
                return
            await asyncio.gather(*tasks)

    def propose(self, intent_id: str = "i1", minutes: int = 10, *, assisted: bool = True) -> None:
        expires = self.clock.now + timedelta(minutes=minutes)
        self.source.proposals = [ProposalObs(intent_id, "Pool pump", expires)]
        self.port.views = [ProposalView(intent_id, "Pool pump", expires, assisted)]


def make(
    config: NotificationsConfig | None = None, conn: StubConnection | None = None
) -> tuple[Rig, NotificationService]:
    clock = Clock()
    stub = conn if conn is not None else StubConnection()
    source, port = Source(clock), Proposals()
    holder: list[NotificationsConfig | None] = [
        config if config is not None else NotificationsConfig(targets=(SERVICE, ENTITY))
    ]

    async def load() -> NotificationsConfig | None:
        return holder[0]

    service = NotificationService(
        connection=stub, source=source, proposals=port, load_config=load, clock=clock, poll_s=3600
    )
    return Rig(service, stub, clock, source, port, holder), service


@pytest.fixture
async def rig() -> AsyncIterator[Rig]:
    r, service = make()
    await service.start()
    await r.drain()
    yield r
    await service.stop()


async def started(config: NotificationsConfig, conn: StubConnection | None = None) -> Rig:
    r, service = make(config, conn)
    await service.start()
    await r.drain()
    return r


def confirm_id(rig: Rig) -> str:
    buttons = rig.conn.pushes("mobile_app_phone")[0]["data"]["actions"]
    return str(next(b["action"] for b in buttons if "CONFIRM" in b["action"]))


def reject_id(rig: Rig) -> str:
    buttons = rig.conn.pushes("mobile_app_phone")[0]["data"]["actions"]
    return str(next(b["action"] for b in buttons if "REJECT" in b["action"]))


# --- a proposal with buttons --------------------------------------------------------------------


async def test_a_proposal_is_pushed_with_buttons_to_a_service_and_plain_to_an_entity(
    rig: Rig,
) -> None:
    assert rig.service.status.actions is ActionsState.ACTIVE
    assert rig.conn.services == ["mobile_app_phone"]
    rig.propose()
    await rig.tick()
    [phone] = rig.conn.pushes("mobile_app_phone")
    assert phone["title"] == "SunCore: confirm Pool pump?"
    assert "10 minutes" in phone["message"]
    assert [b["title"] for b in phone["data"]["actions"]] == ["Confirm", "Reject"]
    [(_, _, entity, target)] = [s for s in rig.conn.sent if s[1] == "send_message"]
    assert target == {"entity_id": ["notify.kitchen_display"]}
    assert "data" not in entity
    assert "Open the SunCore panel" in entity["message"]
    assert rig.service.status.open_buttons == 2
    await rig.tick()
    assert len(rig.conn.pushes("mobile_app_phone")) == 1  # once per proposal


async def test_confirm_goes_through_the_services_layer_once_and_is_idempotent(rig: Rig) -> None:
    rig.propose()
    await rig.tick()
    action = confirm_id(rig)
    rig.conn.tap(action)
    rig.conn.tap(action)  # a double tap or a replayed event
    await rig.drain()
    assert rig.port.confirmed == ["i1"]
    answers = rig.conn.pushes("mobile_app_phone")
    assert answers[-1]["title"] == "SunCore: confirmed"
    rig.conn.tap(action)
    await rig.drain()
    assert rig.port.confirmed == ["i1"]
    assert rig.service.status.open_buttons == 0


async def test_reject_calls_nothing_and_kills_the_confirm_button(rig: Rig) -> None:
    rig.propose()
    await rig.tick()
    confirm, reject = confirm_id(rig), reject_id(rig)
    rig.conn.tap(reject)
    await rig.drain()
    rig.conn.tap(confirm)
    await rig.drain()
    assert rig.port.confirmed == []
    assert rig.conn.pushes("mobile_app_phone")[-1]["title"] == "SunCore: rejected"


async def test_confirm_after_expiry_is_refused_even_if_the_port_still_lists_it(rig: Rig) -> None:
    rig.propose(minutes=10)
    await rig.tick()
    action = confirm_id(rig)
    rig.clock.now += timedelta(minutes=10)
    rig.conn.tap(action)
    await rig.drain()
    assert rig.port.confirmed == []  # the token died with the proposal


async def test_confirm_needs_the_proposal_to_be_current_and_assisted(rig: Rig) -> None:
    rig.propose()
    await rig.tick()
    rig.port.views = []  # the gate no longer has it (panel confirm, dismiss, mode lowered)
    rig.conn.tap(confirm_id(rig))
    await rig.drain()
    assert rig.port.confirmed == []
    assert rig.conn.pushes("mobile_app_phone")[-1]["title"] == "SunCore: proposal no longer valid"


async def test_a_proposal_whose_mode_is_no_longer_assisted_is_not_confirmed(rig: Rig) -> None:
    rig.propose(assisted=False)
    await rig.tick()
    rig.conn.tap(confirm_id(rig))
    await rig.drain()
    assert rig.port.confirmed == []


async def test_the_port_expiry_is_checked_by_the_service_too(rig: Rig) -> None:
    rig.propose(minutes=10)
    await rig.tick()
    action = confirm_id(rig)
    rig.port.views = [
        ProposalView("i1", "Pool pump", rig.clock.now - timedelta(seconds=1), True)
    ]  # the proposal on the port is already past its time, the token not yet
    rig.conn.tap(action)
    await rig.drain()
    assert rig.port.confirmed == []


async def test_a_failing_or_refusing_service_is_reported_not_raised(rig: Rig) -> None:
    rig.propose()
    await rig.tick()
    rig.port.error = RuntimeError("boom")
    rig.conn.tap(confirm_id(rig))
    await rig.drain()
    assert rig.port.confirmed == ["i1"]
    assert rig.conn.pushes("mobile_app_phone")[-1]["title"] == "SunCore: not switched"


async def test_forged_and_foreign_actions_do_nothing(rig: Rig) -> None:
    rig.propose()
    await rig.tick()
    before = len(rig.conn.sent)
    for action in (
        "SUNCORE_CONFIRM_" + "0" * 32,
        "SUNCORE_CONFIRM_i1",
        "SUNCORE_REJECT_" + "f" * 32,
        "OPEN_DOOR",
        "",
    ):
        rig.conn.tap(action)
    await rig.drain()
    assert rig.port.confirmed == []
    assert len(rig.conn.sent) == before  # no answer to a forgery either


async def test_a_token_of_another_proposal_cannot_confirm_this_one(rig: Rig) -> None:
    rig.propose("i1")
    await rig.tick()
    first = confirm_id(rig)
    open_first = list(rig.port.views)
    rig.propose("i2")
    rig.port.views = [*open_first, *rig.port.views]
    await rig.tick()
    rig.conn.tap(first)
    await rig.drain()
    assert rig.port.confirmed == ["i1"]  # bound to i1, never to i2


async def test_a_tap_flood_is_bounded(rig: Rig) -> None:
    rig.propose()
    await rig.tick()
    for n in range(500):
        rig.conn.tap(f"SUNCORE_CONFIRM_{n:032x}")
    await rig.drain()
    assert rig.port.confirmed == []


# --- degraded modes -----------------------------------------------------------------------------


async def test_a_refused_subscription_means_plain_notifications_and_no_taps() -> None:
    conn = StubConnection(subscribe_error=CommandError("unauthorized", "no"))
    r = await started(NotificationsConfig(targets=(SERVICE,)), conn)
    assert r.service.status.actions is ActionsState.REFUSED
    assert r.service.status.reason_key == "notify.reason.actions_refused"
    r.propose()
    await r.tick()
    [push] = r.conn.pushes("mobile_app_phone")
    assert "data" not in push  # no buttons
    assert "Open the SunCore panel" in push["message"]
    assert r.service.status.open_buttons == 0
    await r.service.stop()


async def test_before_the_subscription_succeeds_no_tap_is_honoured() -> None:
    conn = StubConnection(state=ConnectionState.CONNECTING)
    r = await started(NotificationsConfig(targets=(SERVICE,)), conn)
    assert r.service.status.actions is ActionsState.PENDING
    assert r.service.status.reason_key == "notify.reason.actions_offline"
    r.propose()
    await r.tick()
    assert "data" not in r.conn.pushes("mobile_app_phone")[0]
    # the connection comes up: the attempt is made once and succeeds
    conn.state = ConnectionState.CONNECTED
    for listener in conn.state_listeners:
        listener(ConnectionState.CONNECTED)
    await r.drain()
    assert r.service.status.actions is ActionsState.ACTIVE
    await r.service.stop()


async def test_an_offline_subscription_is_retried_on_the_next_connect() -> None:
    conn = StubConnection(subscribe_error=NotConnectedError("offline"))
    r = await started(NotificationsConfig(targets=(SERVICE,)), conn)
    assert r.service.status.actions is ActionsState.PENDING
    conn.subscribe_error = None
    for listener in conn.state_listeners:
        listener(ConnectionState.CONNECTED)
    await r.drain()
    assert r.service.status.actions is ActionsState.ACTIVE
    await r.service.stop()


async def test_entity_only_targets_never_get_buttons() -> None:
    r = await started(NotificationsConfig(targets=(ENTITY,)))
    assert r.service.status.reason_key == "notify.reason.actions_entity_only"
    r.propose()
    await r.tick()
    assert r.service.status.open_buttons == 0
    assert r.conn.sent[-1][1] == "send_message"
    await r.service.stop()


async def test_without_targets_there_is_no_push_and_the_status_says_so() -> None:
    r = await started(NotificationsConfig())
    assert r.service.status.reason_key == "notify.reason.no_targets"
    r.propose()
    await r.tick()
    assert r.conn.sent == []
    await r.service.stop()


async def test_a_delivery_error_is_swallowed_and_does_not_burn_the_token() -> None:
    conn = StubConnection(notify_error=HAError("down"))
    r = await started(NotificationsConfig(targets=(SERVICE,)), conn)
    r.propose()
    await r.tick()
    assert r.service.status.open_buttons == 0  # nobody got the buttons
    await r.service.stop()


# --- categories, quiet hours, cap, persistent ---------------------------------------------------


async def test_a_switched_off_category_sends_nothing() -> None:
    config = NotificationsConfig(
        targets=(SERVICE,), categories=NotificationCategories(proposal=False)
    )
    r = await started(config)
    r.propose()
    await r.tick()
    assert r.conn.sent == []
    await r.service.stop()


async def test_quiet_hours_drop_a_proposal_but_not_an_error(rig: Rig) -> None:
    rig.clock.now = datetime(2026, 11, 24, 23, 0, tzinfo=UTC)
    rig.propose()
    rig.source.planner_failing_s = 7200.0
    await rig.tick()
    assert not [p for p in rig.conn.pushes() if "confirm" in p["title"]]
    assert [p for p in rig.conn.pushes("mobile_app_phone") if "no plan" in p["title"]]


async def test_the_daily_cap_limits_push_but_not_persistent() -> None:
    config = NotificationsConfig(targets=(SERVICE,), daily_push_cap=1)
    r = await started(config)
    r.source.planner_failing_s = 7200.0
    await r.tick()
    r.source.emergency = False
    await r.tick()
    await r.service.submit(  # a second, different safety event
        _event("emergency_stop:9", r.clock.now)
    )
    assert len(r.conn.pushes()) == 1  # the second push is over the cap
    assert len(r.conn.persistents("create")) == 2  # persistent notifications are not capped
    await r.service.stop()


def _event(key: str, now: datetime):  # type: ignore[no-untyped-def]
    return NotifyEvent(Category.SAFETY, key, Reason(key="notify.safety.emergency_stop"), now)


async def test_errors_and_safety_events_are_persistent_and_dismissed_when_resolved(
    rig: Rig,
) -> None:
    rig.source.planner_failing_s = 7200.0
    await rig.tick()
    [created] = rig.conn.persistents("create")
    assert created["notification_id"] == "suncore_planner_failed"
    assert created["title"] == "SunCore: no plan"
    rig.source.planner_failing_s = None
    await rig.tick()
    assert rig.conn.persistents("dismiss") == [{"notification_id": "suncore_planner_failed"}]
    await rig.tick()
    assert len(rig.conn.persistents("dismiss")) == 1


async def test_persistent_notifications_can_be_switched_off() -> None:
    config = NotificationsConfig(targets=(SERVICE,), persistent=False)
    r = await started(config)
    r.source.planner_failing_s = 7200.0
    await r.tick()
    assert r.conn.persistents("create") == []
    assert len(r.conn.pushes()) == 1
    await r.service.stop()


async def test_emergency_stop_notifies_on_the_edge(rig: Rig) -> None:
    await rig.tick()
    rig.source.emergency = True
    await rig.tick()
    assert [p["title"] for p in rig.conn.persistents("create")] == ["SunCore: emergency stop"]
    assert [p["title"] for p in rig.conn.pushes("mobile_app_phone")] == ["SunCore: emergency stop"]


async def test_the_mqtt_refusal_hook_event_is_a_persistent_safety_notice(rig: Rig) -> None:
    event = NotifyEvent(
        Category.SAFETY,
        "mqtt_raise_refused:auto:2026112412",
        Reason(key="notify.safety.mqtt_raise_refused", args={"entity": "auto"}),
        rig.clock.now,
    )
    rig.service.post(event)
    rig.service.post(event)  # the same hour: once
    await rig.drain()
    [created] = rig.conn.persistents("create")
    assert "Home Assistant (auto)" in created["message"]


# --- test message and config reload -------------------------------------------------------------


async def test_the_test_message_reaches_every_target_and_is_rate_limited(rig: Rig) -> None:
    result = await rig.service.send_test()
    assert (result.sent, result.failed, result.reason_key) == (2, 0, None)
    assert rig.conn.pushes("mobile_app_phone")[-1]["title"] == "SunCore: test"
    again = await rig.service.send_test()
    assert again.reason_key == "notify.reason.test_rate_limited"
    assert again.sent == 0


async def test_the_test_message_without_targets_says_why() -> None:
    r = await started(NotificationsConfig())
    assert (await r.service.send_test()).reason_key == "notify.reason.no_targets"
    await r.service.stop()


async def test_reconfigure_picks_up_new_targets_and_quiet_hours(rig: Rig) -> None:
    rig.config[0] = NotificationsConfig(
        targets=(NotifyTarget(kind="service", id="mobile_app_tablet"),),
        quiet_hours=QuietHours(enabled=False),
    )
    rig.service.reconfigure()
    await rig.drain()
    assert rig.conn.services == ["mobile_app_tablet"]
    assert rig.service.status.push_targets == 1


def test_the_verbs_are_the_two_documented_ones() -> None:
    assert {v.value for v in Verb} == {"CONFIRM", "REJECT"}
