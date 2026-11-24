# SPDX-License-Identifier: Apache-2.0
"""Dedupe, minimum interval, quiet hours and the daily cap."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from suncore.core.config import NotificationsConfig, NotifyTarget, QuietHours
from suncore.core.decisions import Reason
from suncore.notify.model import Category, NotifyEvent, Verdict
from suncore.notify.ratelimit import NotifyLimiter, in_quiet_hours

BERLIN = ZoneInfo("Europe/Berlin")
NOON = datetime(2026, 11, 24, 12, 0, tzinfo=BERLIN)
TARGET = (NotifyTarget(kind="service", id="mobile_app_phone"),)


def config(**kw: object) -> NotificationsConfig:
    return NotificationsConfig(targets=TARGET, **kw)  # type: ignore[arg-type]


def event(category: Category = Category.PROPOSAL, key: str = "k", **kw: object) -> NotifyEvent:
    return NotifyEvent(category, key, Reason(key="notify.test"), NOON, **kw)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("hour", "minute", "quiet"),
    [(21, 59, False), (22, 0, True), (23, 30, True), (0, 0, True), (6, 59, True), (7, 0, False)],
)
def test_quiet_hours_wrap_midnight(hour: int, minute: int, quiet: bool) -> None:
    moment = datetime(2026, 11, 24, hour, minute, tzinfo=BERLIN)
    assert in_quiet_hours(QuietHours(), moment, BERLIN) is quiet


def test_quiet_hours_use_local_time_and_can_be_switched_off() -> None:
    utc = datetime(2026, 11, 24, 21, 30, tzinfo=UTC)  # 22:30 in Berlin
    assert in_quiet_hours(QuietHours(), utc, BERLIN)
    assert not in_quiet_hours(QuietHours(), utc, UTC)
    assert not in_quiet_hours(QuietHours(enabled=False), utc, BERLIN)


def test_same_day_window() -> None:
    quiet = QuietHours(start="13:00", end="15:00")
    assert in_quiet_hours(quiet, datetime(2026, 1, 1, 14, 0, tzinfo=UTC))
    assert not in_quiet_hours(quiet, datetime(2026, 1, 1, 15, 0, tzinfo=UTC))


def test_no_targets_means_no_push() -> None:
    assert (
        NotifyLimiter().judge_push(event(), NotificationsConfig(), NOON, BERLIN) is Verdict.DISABLED
    )


def test_a_key_is_announced_once_when_the_interval_is_zero() -> None:
    limiter = NotifyLimiter()
    e = event()
    assert limiter.judge_push(e, config(), NOON, BERLIN) is Verdict.SEND
    limiter.commit(e, NOON, BERLIN, pushed=True)
    later = NOON + timedelta(days=3)
    assert limiter.judge_push(e, config(), later, BERLIN) is Verdict.DUPLICATE
    other = event(key="k2")
    assert limiter.judge_push(other, config(), NOON, BERLIN) is Verdict.SEND


def test_errors_repeat_after_thirty_minutes() -> None:
    limiter = NotifyLimiter()
    e = event(Category.ERROR, "err")
    limiter.commit(e, NOON, BERLIN, pushed=True)
    assert limiter.is_duplicate(e, NOON + timedelta(minutes=29))
    assert not limiter.is_duplicate(e, NOON + timedelta(minutes=30))


def test_an_undelivered_message_does_not_use_up_the_key() -> None:
    limiter = NotifyLimiter()
    e = event()
    assert limiter.judge_push(e, config(), NOON, BERLIN) is Verdict.SEND
    assert limiter.judge_push(e, config(), NOON, BERLIN) is Verdict.SEND  # nothing committed


def test_quiet_hours_apply_by_category_and_can_be_overridden() -> None:
    limiter = NotifyLimiter()
    night = datetime(2026, 11, 24, 23, 0, tzinfo=BERLIN)
    assert limiter.judge_push(event(), config(), night, BERLIN) is Verdict.QUIET_HOURS
    assert limiter.judge_push(event(Category.ERROR), config(), night, BERLIN) is Verdict.SEND
    assert limiter.judge_push(event(Category.SAFETY), config(), night, BERLIN) is Verdict.SEND
    forced = event(quiet_hours=False)
    assert limiter.judge_push(forced, config(), night, BERLIN) is Verdict.SEND
    program_done = event(Category.PROGRAM, quiet_hours=True)
    assert limiter.judge_push(program_done, config(), night, BERLIN) is Verdict.QUIET_HOURS


def test_the_daily_cap_counts_pushes_and_resets_at_local_midnight() -> None:
    limiter = NotifyLimiter()
    cfg = config(daily_push_cap=2)
    for n in range(2):
        e = event(key=f"k{n}")
        assert limiter.judge_push(e, cfg, NOON, BERLIN) is Verdict.SEND
        limiter.commit(e, NOON, BERLIN, pushed=True)
    assert limiter.judge_push(event(key="k9"), cfg, NOON, BERLIN) is Verdict.DAILY_CAP
    assert limiter.pushed_today == 2
    tomorrow = NOON + timedelta(days=1)
    assert limiter.judge_push(event(key="k9"), cfg, tomorrow, BERLIN) is Verdict.SEND
    assert limiter.pushed_today == 0


def test_a_cap_exempt_event_is_sent_beyond_the_daily_cap() -> None:
    """Review 6-L L1: a program's "start now" and "missed" are never dropped by the cap."""
    limiter = NotifyLimiter()
    cfg = config(daily_push_cap=1)
    first = event(key="k0")
    limiter.commit(first, NOON, BERLIN, pushed=True)
    assert limiter.judge_push(event(key="k1"), cfg, NOON, BERLIN) is Verdict.DAILY_CAP
    exempt = event(Category.PROGRAM, key="k2", cap_exempt=True)
    assert limiter.judge_push(exempt, cfg, NOON, BERLIN) is Verdict.SEND


def test_a_persistent_only_delivery_does_not_count_against_the_cap() -> None:
    limiter = NotifyLimiter()
    limiter.commit(event(), NOON, BERLIN, pushed=False)
    assert limiter.pushed_today == 0


def test_the_remembered_keys_are_bounded() -> None:
    limiter = NotifyLimiter()
    for n in range(600):
        limiter.commit(event(key=f"k{n}"), NOON, BERLIN, pushed=False)
    assert not limiter.is_duplicate(event(key="k0"), NOON)  # the oldest were dropped
    assert limiter.is_duplicate(event(key="k599"), NOON)
