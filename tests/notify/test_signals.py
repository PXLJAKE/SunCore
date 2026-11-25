# SPDX-License-Identifier: Apache-2.0
"""The notification events that are MQTT event types too (PR 7-H)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from suncore.core.config import NotificationCategories, NotificationsConfig
from suncore.core.decisions import Reason
from suncore.ha.mqtt.catalogue import EVENT_TYPES
from suncore.notify import Category, EvTargetObs, NotifyEvent, Observation, RuleEngine, signal_for
from suncore.notify.signals import _BY_TEXT_KEY, CHEAP_WINDOW_END

from .test_rules import T0, slots
from .test_service import SERVICE, started

NOW = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)


def event(key: str, **args: str | int | float) -> NotifyEvent:
    return NotifyEvent(Category.SAFETY, "k", Reason(key=key, args=args), NOW)


@pytest.mark.parametrize(
    ("text_key", "kind"),
    [
        ("notify.safety.island_start", "island_start"),
        ("notify.safety.island_end", "island_end"),
        ("notify.safety.para14a_start", "para14a_start"),
        ("notify.safety.para14a_end", "para14a_end"),
        ("notify.cheap_window.start", "cheap_window_start"),
        ("notify.program.start_now", "program_start_now"),
        ("notify.program.done", "program_done"),
        ("notify.ev_target.at_risk", "ev_target_at_risk"),
        ("notify.ev_target.at_risk_short", "ev_target_at_risk"),
    ],
)
def test_every_event_type_has_one_notification_behind_it(text_key: str, kind: str) -> None:
    signal = signal_for(event(text_key))
    assert signal is not None
    assert signal.type == kind


def test_no_event_type_is_invented() -> None:
    produced = {*_BY_TEXT_KEY.values(), CHEAP_WINDOW_END}
    assert produced <= set(EVENT_TYPES)
    # these four come from the gate's decisions (device_switched, proposal, error) or are
    # still without a producer (proposal_expired)
    assert set(EVENT_TYPES) - produced == {
        "device_switched",
        "proposal",
        "proposal_expired",
        "error",
    }


@pytest.mark.parametrize(
    "text_key",
    [
        "notify.program.missed",
        "notify.program.unknown",
        "notify.error.write_failed",
        "notify.safety.mqtt_raise_refused",
        "notify.daily_summary.text",
    ],
)
def test_notifications_without_an_event_type_give_no_signal(text_key: str) -> None:
    assert signal_for(event(text_key)) is None


def test_attributes_are_the_plain_arguments_and_the_device_is_a_name() -> None:
    cheap = signal_for(event("notify.cheap_window.start", start="14:00", end="15:15", price=0.1))
    assert cheap is not None
    assert dict(cheap.attrs) == {"start": "14:00", "end": "15:15", "price": 0.1}
    program = signal_for(event("notify.program.done", device="Washer", noise="x"))
    assert program is not None
    assert dict(program.attrs) == {"name": "Washer"}


def test_a_cheap_window_ends_once_when_its_end_has_passed() -> None:
    engine = RuleEngine()
    prices: list[float | None] = [0.30] * 8 + [0.10] * 5 + [0.30] * 20

    def at(when: datetime) -> Observation:
        return Observation(
            now=when, emergency_stop=False, global_mode="assisted", prices=slots(prices)
        )

    assert engine.evaluate(at(T0)).signals == ()
    assert engine.evaluate(at(T0 + timedelta(hours=3))).signals == ()  # 15:00, ends 15:15
    after = engine.evaluate(at(T0 + timedelta(hours=3, minutes=15)))
    assert [(s.type, dict(s.attrs)) for s in after.signals] == [
        ("cheap_window_end", {"start": "14:00", "end": "15:15"})
    ]
    assert engine.evaluate(at(T0 + timedelta(hours=4))).signals == ()


async def test_signals_fire_in_quiet_hours_past_the_cap_and_for_a_silenced_category() -> None:
    """The MQTT event does not follow the push rules: quiet hours, the daily cap and the category
    switch only govern the push; every start and end reaches the listener exactly once."""
    config = NotificationsConfig(
        targets=(SERVICE,),
        daily_push_cap=1,
        categories=NotificationCategories(ev_target=False),
    )
    r = await started(config)
    seen: list[tuple[str, dict[str, object]]] = []
    r.service.add_signal_listener(lambda s: seen.append((s.type, dict(s.attrs))))
    r.clock.now = datetime(2026, 11, 24, 23, 0, tzinfo=UTC)  # quiet hours
    r.source.island, r.source.para14a = False, False
    await r.tick()  # baseline
    r.source.island = True
    await r.tick()
    by = datetime(2026, 11, 25, 6, 0, tzinfo=UTC)
    r.source.ev_targets = [EvTargetObs("wb", "Garage charger", by, True, 20.0, 14.5)]
    await r.tick()
    r.clock.now += timedelta(minutes=11)
    await r.tick()  # the category is off: no push, but the event fires
    r.source.island = False
    await r.tick()
    r.clock.now += timedelta(minutes=3)
    await r.tick()  # off for more than the 120 s of flicker protection
    assert [t for t, _ in seen] == ["island_start", "ev_target_at_risk", "island_end"]
    assert seen[1][1] == {
        "name": "Garage charger",
        "time": "06:00",
        "need_kwh": 20.0,
        "planned_kwh": 14.5,
    }
    await r.service.stop()


async def test_a_posted_program_event_is_a_signal_and_a_failing_listener_is_contained() -> None:
    r = await started(NotificationsConfig(targets=(SERVICE,)))
    seen: list[str] = []

    def broken(_signal: object) -> None:
        raise RuntimeError("listener bug")

    r.service.add_signal_listener(broken)
    remove = r.service.add_signal_listener(lambda s: seen.append(s.type))

    def notice(key: str) -> NotifyEvent:
        return NotifyEvent(
            Category.PROGRAM,
            f"program:j1:{key}",
            Reason(key=f"notify.program.{key}", args={"device": "Washer", "deadline": "17:00"}),
            r.clock.now,
        )

    await r.service.submit(notice("start_now"))
    await r.service.submit(notice("done"))
    await r.service.submit(notice("missed"))  # a push only: no event type
    assert seen == ["program_start_now", "program_done"]
    remove()
    await r.service.submit(notice("done"))
    assert seen == ["program_start_now", "program_done"]
    await r.service.stop()
