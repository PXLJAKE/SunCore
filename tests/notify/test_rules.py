# SPDX-License-Identifier: Apache-2.0
"""Rules per category: transitions, baselines, thresholds, cheap windows."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from suncore.notify.model import Category
from suncore.notify.rules import (
    DeviceObs,
    Observation,
    ProposalObs,
    RuleEngine,
    cheap_window,
)

BERLIN = ZoneInfo("Europe/Berlin")
T0 = datetime(2026, 11, 24, 12, 0, tzinfo=BERLIN)


def obs(now: datetime = T0, **kw: object) -> Observation:
    base: dict[str, object] = {"emergency_stop": False, "global_mode": "assisted"}
    base.update(kw)
    return Observation(now=now, **base)  # type: ignore[arg-type]


def device(kind: str | None, ts: datetime | None, *, tripped: bool = False) -> DeviceObs:
    return DeviceObs("pump", "Pool pump", kind, ts, tripped)


def keys(engine: RuleEngine, observation: Observation) -> list[str]:
    return [e.key for e in engine.evaluate(observation).events]


# --- proposals -----------------------------------------------------------------------------


def test_a_new_proposal_is_announced_once_with_its_expiry() -> None:
    engine = RuleEngine()
    proposal = ProposalObs("i1", "Pool pump", T0 + timedelta(minutes=10))
    first = engine.evaluate(obs(proposals=[proposal]))
    assert [e.key for e in first.events] == ["proposal:i1"]
    event = first.events[0]
    assert event.category is Category.PROPOSAL
    assert event.proposal is not None
    assert event.proposal.intent_id == "i1"
    assert event.reason.args == {"device": "Pool pump", "minutes": 10}
    assert keys(engine, obs(T0 + timedelta(seconds=5), proposals=[proposal])) == []


def test_a_proposal_that_ended_can_be_announced_again_under_a_new_id_only() -> None:
    engine = RuleEngine()
    p1 = ProposalObs("i1", "Pool pump", T0 + timedelta(minutes=10))
    engine.evaluate(obs(proposals=[p1]))
    engine.evaluate(obs())
    assert keys(
        engine, obs(proposals=[ProposalObs("i2", "Pool pump", T0 + timedelta(hours=1))])
    ) == ["proposal:i2"]


# --- errors ------------------------------------------------------------------------------------


def test_three_failed_writes_in_a_row_make_an_error_and_a_success_resolves_it() -> None:
    engine = RuleEngine()
    engine.evaluate(obs(devices=[device("accepted", T0)]))  # baseline
    seen: list[str] = []
    for n in range(1, 4):
        ts = T0 + timedelta(minutes=n)
        seen += keys(engine, obs(ts, devices=[device("failed", ts)]))
    assert seen == ["write_failed:pump"]
    ts = T0 + timedelta(minutes=4)
    result = engine.evaluate(obs(ts, devices=[device("accepted", ts)]))
    assert "write_failed:pump" in result.resolved


def test_a_success_between_failures_resets_the_count() -> None:
    engine = RuleEngine()
    engine.evaluate(obs(devices=[device("accepted", T0)]))
    kinds = ["failed", "failed", "accepted", "failed", "failed"]
    seen: list[str] = []
    for n, kind in enumerate(kinds, 1):
        ts = T0 + timedelta(minutes=n)
        seen += keys(engine, obs(ts, devices=[device(kind, ts)]))
    assert seen == []


def test_the_same_decision_seen_twice_counts_once() -> None:
    engine = RuleEngine()
    engine.evaluate(obs(devices=[device("accepted", T0)]))
    ts = T0 + timedelta(minutes=1)
    for _ in range(5):
        assert keys(engine, obs(ts, devices=[device("failed", ts)])) == []


def test_a_watchdog_trip_is_an_error_but_not_at_start() -> None:
    engine = RuleEngine()
    assert keys(engine, obs(devices=[device(None, None, tripped=True)])) == []  # baseline
    engine.evaluate(obs(devices=[device(None, None)]))
    assert keys(engine, obs(devices=[device(None, None, tripped=True)])) == ["tripped:pump"]
    cleared = engine.evaluate(obs(devices=[device(None, None)]))
    assert cleared.resolved == ("tripped:pump",)


def test_the_planner_error_needs_an_hour() -> None:
    engine = RuleEngine()
    assert keys(engine, obs(planner_failing_s=3599.0)) == []
    assert keys(engine, obs(planner_failing_s=3600.0)) == ["planner_failed"]
    assert "planner_failed" in engine.evaluate(obs(planner_failing_s=None)).resolved


def test_mqtt_lost_needs_fifteen_minutes_of_a_wanted_connection() -> None:
    engine = RuleEngine()
    assert keys(engine, obs(mqtt_enabled=True, mqtt_connected=False)) == []
    soon = T0 + timedelta(minutes=14, seconds=59)
    assert keys(engine, obs(soon, mqtt_enabled=True, mqtt_connected=False)) == []
    late = T0 + timedelta(minutes=15)
    assert keys(engine, obs(late, mqtt_enabled=True, mqtt_connected=False)) == ["mqtt_lost"]
    assert (
        "mqtt_lost" in engine.evaluate(obs(late, mqtt_enabled=True, mqtt_connected=True)).resolved
    )
    # not enabled: never an error, and a later outage starts counting from scratch
    assert keys(engine, obs(late, mqtt_enabled=False)) == []
    assert keys(engine, obs(late + timedelta(minutes=1), mqtt_enabled=True)) == []


# --- safety ----------------------------------------------------------------------------------


def test_emergency_stop_transitions_but_not_the_initial_state() -> None:
    engine = RuleEngine()
    assert keys(engine, obs(emergency_stop=True)) == []  # already true at start
    cleared = engine.evaluate(obs(emergency_stop=False))
    assert [e.key for e in cleared.events] == ["emergency_stop_cleared:0"]
    assert cleared.events[0].persist is False
    again = engine.evaluate(obs(emergency_stop=True))
    assert [e.key for e in again.events] == ["emergency_stop:1"]
    assert again.events[0].category is Category.SAFETY
    done = engine.evaluate(obs(emergency_stop=False))
    assert "emergency_stop:1" in done.resolved


def test_island_and_para14a_are_announced_on_the_edges_only_when_a_source_exists() -> None:
    engine = RuleEngine()
    assert keys(engine, obs(island=None, para14a=None)) == []
    engine.evaluate(obs(island=False, para14a=False))
    assert keys(engine, obs(island=True, para14a=False)) == ["island:1"]
    assert keys(engine, obs(island=True, para14a=True)) == ["para14a:1"]
    end = engine.evaluate(obs(island=False, para14a=True))
    assert [e.key for e in end.events] == ["island_end:1"]
    assert "island:1" in end.resolved
    assert keys(engine, obs(island=None, para14a=True)) == []  # source gone: no event


# --- cheap windows and the summary -------------------------------------------------------------


def slots(prices: list[float | None], start: datetime = T0) -> list[tuple[datetime, float | None]]:
    return [(start + timedelta(minutes=15 * i), p) for i, p in enumerate(prices)]


def test_cheapest_contiguous_block_below_the_25th_percentile() -> None:
    prices: list[float | None] = [0.30] * 8 + [0.10] * 5 + [0.30] * 8 + [0.12] * 4 + [0.30] * 8
    window = cheap_window(slots(prices), T0)
    assert window is not None
    start, end, mean = window
    assert start == T0 + timedelta(minutes=15 * 8)
    assert end - start == timedelta(minutes=15 * 5)
    assert mean == 0.10


def test_a_block_shorter_than_an_hour_is_no_window() -> None:
    prices: list[float | None] = [0.30] * 10 + [0.10] * 3 + [0.30] * 10
    assert cheap_window(slots(prices), T0) is None


def test_a_missing_price_breaks_a_block() -> None:
    prices: list[float | None] = [0.30] * 8 + [0.10, 0.10, None, 0.10, 0.10] + [0.30] * 8
    assert cheap_window(slots(prices), T0) is None


def test_slots_beyond_24_hours_are_ignored() -> None:
    far = T0 + timedelta(hours=30)
    assert cheap_window(slots([0.1] * 8 + [0.5] * 8, far), T0) is None


def test_a_cheap_window_is_announced_once() -> None:
    engine = RuleEngine()
    prices: list[float | None] = [0.30] * 8 + [0.10] * 5 + [0.30] * 20
    first = engine.evaluate(obs(prices=slots(prices)))
    assert [e.category for e in first.events] == [Category.CHEAP_WINDOW]
    event = first.events[0]
    assert event.reason.args["start"] == "14:00"  # eight slots after noon
    assert event.reason.args["end"] == "15:15"
    assert engine.evaluate(obs(T0 + timedelta(minutes=5), prices=slots(prices))).events == ()


def test_the_daily_summary_comes_once_after_eight_in_the_evening() -> None:
    engine = RuleEngine()
    afternoon = T0.replace(hour=19, minute=59)
    assert keys(engine, obs(afternoon)) == []
    evening = T0.replace(hour=20)
    assert keys(engine, obs(evening)) == ["summary:2026-11-24"]
    assert keys(engine, obs(evening + timedelta(minutes=5))) == []
    assert keys(engine, obs(evening + timedelta(days=1))) == ["summary:2026-11-25"]


def test_the_summary_counts_the_switchings_of_the_day() -> None:
    engine = RuleEngine()
    engine.evaluate(obs(devices=[device("accepted", T0)]))
    for n in range(1, 3):
        ts = T0 + timedelta(minutes=n)
        engine.evaluate(obs(ts, devices=[device("accepted", ts)]))
    last = T0 + timedelta(minutes=2)
    summary = engine.evaluate(obs(T0.replace(hour=20), devices=[device("accepted", last)]))
    assert summary.events[0].reason.args["switched"] == 2
