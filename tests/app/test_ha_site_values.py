# SPDX-License-Identifier: Apache-2.0
"""PR 7-H: the five site sensors and the MQTT events, from the plan, the day summary and the
accuracy rows (pure functions) and from the runtime (producers)."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from suncore.app.ha_entities import SUPPORTED, RuntimeEntitySource, SiteEventBuffer
from suncore.app.ha_site_values import (
    autarky_today_pct,
    next_action,
    plan_cost_today,
    pv_forecast_error_w,
    savings_today,
)
from suncore.app.runtime import Runtime
from suncore.core.control import IntervalFlows, IntervalPrices, Plan, PlanInterval
from suncore.core.decisions import Reason
from suncore.ha.mqtt import SiteEvent
from suncore.notify import SiteSignal
from suncore.storage.summaries import AccuracyDayRow

BERLIN = ZoneInfo("Europe/Berlin")
NOW = datetime(2026, 11, 25, 12, 0, tzinfo=BERLIN)
Q = timedelta(minutes=15)


def interval(
    start: datetime,
    *,
    import_w: float = 0.0,
    export_w: float = 0.0,
    prices: tuple[float, float] | None = (0.30, 0.08),
    reasons: tuple[Reason, ...] = (),
) -> PlanInterval:
    return PlanInterval(
        start=start,
        end=start + Q,
        expected=IntervalFlows(pv_p50_w=0, load_w=0, import_w=import_w, export_w=export_w),
        prices=None
        if prices is None
        else IntervalPrices(import_price=prices[0], export_price=prices[1]),
        reasons=reasons,
    )


# --- plan_cost_today -------------------------------------------------------------------------


def test_the_plan_cost_is_the_grid_balance_of_the_local_day_in_currency() -> None:
    plan = Plan(
        intervals=(
            interval(NOW, import_w=2000.0),  # 0.5 kWh at 0.30
            interval(NOW + Q, export_w=4000.0),  # 1 kWh at 0.08
            interval(NOW + timedelta(days=1), import_w=9000.0),  # tomorrow: not counted
        )
    )
    assert plan_cost_today(plan, NOW, BERLIN) == pytest.approx(0.5 * 0.30 - 1.0 * 0.08)


def test_the_plan_cost_follows_the_local_day_not_utc() -> None:
    late = datetime(2026, 11, 25, 23, 30, tzinfo=BERLIN)  # 22:30 UTC, same Berlin day
    plan = Plan(intervals=(interval(late, import_w=4000.0),))
    assert plan_cost_today(plan, NOW.astimezone(UTC), BERLIN) == pytest.approx(0.30)
    assert plan_cost_today(plan, NOW.astimezone(UTC), UTC) == pytest.approx(0.30)


@pytest.mark.parametrize(
    "plan",
    [
        None,
        Plan.default(),
        Plan(intervals=(interval(NOW, import_w=1000.0, prices=None),)),
        Plan(intervals=(interval(NOW + timedelta(days=2), import_w=1000.0),)),
    ],
)
def test_no_priced_interval_today_gives_no_cost(plan: Plan | None) -> None:
    assert plan_cost_today(plan, NOW, BERLIN) is None


# --- next_action -----------------------------------------------------------------------------

CHARGE = Reason(key="planner.battery.charge_surplus", args={"power_w": 2400.0, "soc_pct": 55.0})
BALANCED = Reason(key="planner.interval.balanced")


def test_the_next_action_is_the_first_reason_of_the_next_busy_interval_in_both_languages() -> None:
    plan = Plan(
        intervals=(
            interval(NOW - timedelta(hours=1), reasons=(CHARGE,)),  # over
            interval(NOW, reasons=(BALANCED,)),  # nothing to do
            interval(NOW + Q, reasons=()),
            interval(NOW + 2 * Q, reasons=(CHARGE,)),
        )
    )
    en = next_action(plan, NOW, BERLIN, "en")
    de = next_action(plan, NOW, BERLIN, "de")
    assert en == "12:30: Store 2,400 W of solar surplus (battery 55.0 %)."
    assert de is not None
    assert de.startswith("12:30 Uhr: ")
    assert "2.400" in de


def test_the_next_action_names_tomorrow_and_later_days() -> None:
    tomorrow = Plan(intervals=(interval(NOW + timedelta(days=1), reasons=(CHARGE,)),))
    later = Plan(intervals=(interval(NOW + timedelta(days=3), reasons=(CHARGE,)),))
    assert (next_action(tomorrow, NOW, BERLIN, "en") or "").startswith("Tomorrow 12:00: ")
    assert (next_action(tomorrow, NOW, BERLIN, "de") or "").startswith("Morgen 12:00 Uhr: ")
    assert (next_action(later, NOW, BERLIN, "en") or "").startswith("2026-11-28 12:00: ")


def test_the_running_interval_counts_and_a_long_text_fits_the_state_limit() -> None:
    running = interval(NOW - timedelta(minutes=5), reasons=(CHARGE,))
    text = next_action(Plan(intervals=(running,)), NOW, BERLIN, "en")
    assert text is not None
    assert text.startswith("11:55: ")
    long = Reason(key="planner.battery.user_target", args={"soc_pct": 80.0, "time": "x" * 400})
    clipped = next_action(Plan(intervals=(interval(NOW, reasons=(long,)),)), NOW, BERLIN, "en")
    assert clipped is not None
    assert len(clipped) == 255
    assert clipped.endswith("…")
    unknown = Plan(intervals=(interval(NOW, reasons=(Reason(key="planner.no.such.key"),)),))
    assert next_action(unknown, NOW, BERLIN, "en") == "12:00: planner.no.such.key"


@pytest.mark.parametrize(
    "plan",
    [
        None,
        Plan.default(),
        Plan(intervals=(interval(NOW, reasons=(BALANCED,)),)),
        Plan(intervals=(interval(NOW - timedelta(hours=2), reasons=(CHARGE,)),)),
    ],
)
def test_nothing_planned_gives_no_next_action(plan: Plan | None) -> None:
    assert next_action(plan, NOW, BERLIN, "en") is None


# --- day values ------------------------------------------------------------------------------


def row(**kw: Any) -> Any:
    base = {"savings_vs_no_pv": 3.456, "autarky": 0.734}
    return SimpleNamespace(**{**base, **kw})


def test_savings_and_autarky_come_from_today_summary() -> None:
    assert savings_today(row()) == 3.456
    assert autarky_today_pct(row()) == pytest.approx(73.4)
    assert autarky_today_pct(row(autarky=0.0)) == 0.0


def test_a_missing_row_or_value_is_no_value() -> None:
    assert savings_today(None) is None
    assert savings_today(row(savings_vs_no_pv=None)) is None
    assert autarky_today_pct(None) is None
    assert autarky_today_pct(row(autarky=None)) is None


def acc(  # noqa: PLR0917 - a compact fixture helper
    source: str, day: int, horizon: str, mae: float, n: int, kind: str = "pv"
) -> AccuracyDayRow:
    return AccuracyDayRow(
        source_id=source,
        kind=kind,  # type: ignore[arg-type]
        day=date(2026, 11, day),
        horizon=horizon,
        mae_w=mae,
        bias_w=0.0,
        n=n,
        computed_at=NOW,
    )


def test_the_pv_forecast_error_is_the_weighted_mae_of_the_newest_day_of_the_blend() -> None:
    rows = [
        acc("blended", 23, "0_6h", 900.0, 10),
        acc("blended", 24, "0_6h", 100.0, 30),
        acc("blended", 24, "6_24h", 300.0, 10),
        acc("solcast", 24, "0_6h", 5000.0, 40),  # not the blend: ignored while the blend has rows
        acc("blended", 24, "24_48h", 0.0, 0),  # no samples
        acc("blended", 24, "0_6h", 7777.0, 5, kind="load"),
    ]
    assert pv_forecast_error_w(rows, "blended") == pytest.approx((100 * 30 + 300 * 10) / 40)


def test_without_the_blend_the_pv_sources_count_and_without_rows_there_is_no_value() -> None:
    assert pv_forecast_error_w([acc("solcast", 24, "0_6h", 200.0, 4)], "blended") == 200.0
    assert pv_forecast_error_w([], "blended") is None
    assert pv_forecast_error_w([acc("blended", 24, "0_6h", 1.0, 3, kind="load")], "blended") is None


# --- the sensors are announced ----------------------------------------------------------------


def test_the_five_site_sensors_are_supported_and_the_month_sum_is_not() -> None:
    for key in ("next_action", "plan_cost_today", "savings_today", "autarky_today"):
        assert key in SUPPORTED
    assert "pv_forecast_error" in SUPPORTED
    assert "savings_month" not in SUPPORTED


# --- the runtime source ----------------------------------------------------------------------


async def test_the_source_reads_plan_values_directly_and_day_values_once_refreshed(
    demo_runtime: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = demo_runtime.clock()
    plan = Plan(
        intervals=(
            PlanInterval(
                start=now,
                end=now + Q,
                expected=IntervalFlows(pv_p50_w=0, load_w=0, import_w=4000.0, export_w=0.0),
                prices=IntervalPrices(import_price=0.25, export_price=0.05),
                reasons=(CHARGE,),
            ),
        )
    )
    monkeypatch.setattr(
        demo_runtime, "planning", SimpleNamespace(plan=plan, status=lambda *_a, **_k: None)
    )
    calls: list[str] = []

    async def today(_self: Runtime) -> Any:
        calls.append("today")
        return row()

    async def accuracy(_self: Runtime) -> tuple[list[AccuracyDayRow], str]:
        calls.append("accuracy")
        return [acc("blended", 24, "0_6h", 150.0, 8)], "blended"

    monkeypatch.setattr(Runtime, "today_summary", today)
    monkeypatch.setattr(Runtime, "recent_pv_accuracy", accuracy)
    source = RuntimeEntitySource(demo_runtime)
    first = source.values()
    assert "plan_cost_today" in first
    assert first["plan_cost_today"] == pytest.approx(0.25)
    assert isinstance(first["next_action"], str)
    assert "savings_today" not in first  # the day values are read in the background
    task = source._day_task
    assert task is not None
    await task
    values = source.values()
    assert values["savings_today"] == 3.456
    assert values["autarky_today"] == pytest.approx(73.4)
    assert values["pv_forecast_error"] == 150.0
    await asyncio.sleep(0)
    source.values()
    assert calls == ["today", "accuracy"]  # not read again within the refresh interval


async def test_a_failing_day_read_keeps_the_last_values(
    demo_runtime: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = RuntimeEntitySource(demo_runtime)

    async def good(_self: Runtime) -> Any:
        return row()

    async def none(_self: Runtime) -> tuple[list[AccuracyDayRow], str]:
        return [], "blended"

    monkeypatch.setattr(Runtime, "today_summary", good)
    monkeypatch.setattr(Runtime, "recent_pv_accuracy", none)
    await source.refresh_day_values()
    assert source._day == {"savings_today": 3.456, "autarky_today": pytest.approx(73.4)}

    async def broken(_self: Runtime) -> Any:
        raise OSError("db locked")

    monkeypatch.setattr(Runtime, "today_summary", broken)
    await source.refresh_day_values()
    assert source._day["savings_today"] == 3.456


async def test_no_plan_and_no_day_data_are_no_values(
    demo_runtime: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(demo_runtime, "planning", None)

    async def nothing(_self: Runtime) -> None:
        return None

    async def no_rows(_self: Runtime) -> tuple[list[AccuracyDayRow], str]:
        return [], "blended"

    monkeypatch.setattr(Runtime, "today_summary", nothing)
    monkeypatch.setattr(Runtime, "recent_pv_accuracy", no_rows)
    source = RuntimeEntitySource(demo_runtime)
    await source.refresh_day_values()
    values = source.values()
    for key in ("plan_cost_today", "next_action", "savings_today", "autarky_today"):
        assert key not in values
    assert "pv_forecast_error" not in values


# --- events ----------------------------------------------------------------------------------


async def test_signals_of_the_notification_rules_reach_the_event_entity_source(
    demo_runtime: Runtime,
) -> None:
    buffer = SiteEventBuffer(demo_runtime.clock)
    source = RuntimeEntitySource(demo_runtime, buffer)
    source.drain_events()  # the decision baseline
    buffer.offer(SiteEvent("island_start"))
    buffer.offer(SiteEvent("program_done", {"name": "Washer"}))
    events = source.drain_events()
    assert [(e.type, dict(e.attrs)) for e in events] == [
        ("island_start", {}),
        ("program_done", {"name": "Washer"}),
    ]
    assert source.drain_events() == []


def test_the_event_buffer_is_bounded_and_drops_stale_events() -> None:
    clock = [NOW]
    buffer = SiteEventBuffer(lambda: clock[0])
    buffer.offer(SiteEvent("island_start"))
    clock[0] = NOW + timedelta(minutes=6)  # the broker was away
    buffer.offer(SiteEvent("island_end"))
    assert [e.type for e in buffer.drain()] == ["island_end"]
    for _ in range(100):
        buffer.offer(SiteEvent("cheap_window_start"))
    assert len(buffer.drain()) == 64


async def test_the_runtime_forwards_a_signal_to_the_buffer_and_wakes_the_publisher(
    demo_runtime: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    buffer = SiteEventBuffer(demo_runtime.clock)
    woken: list[bool] = []
    monkeypatch.setattr(demo_runtime, "site_events", buffer)
    monkeypatch.setattr(
        demo_runtime, "ha_entities", SimpleNamespace(refresh=lambda: woken.append(True))
    )
    demo_runtime._on_site_signal(SiteSignal("ev_target_at_risk", {"name": "Wallbox"}))
    assert woken == [True]
    [event] = buffer.drain()
    assert (event.type, dict(event.attrs)) == ("ev_target_at_risk", {"name": "Wallbox"})
    monkeypatch.setattr(demo_runtime, "site_events", None)
    demo_runtime._on_site_signal(SiteSignal("island_start"))  # demo mode: nothing to do
    assert woken == [True]


# --- the runtime reads (real time series) ------------------------------------------------------


async def test_the_runtime_reads_recent_pv_accuracy_rows_and_today_summary(
    demo_runtime: Runtime,
) -> None:
    timeseries = demo_runtime.timeseries
    assert timeseries is not None
    tz = demo_runtime.timezone()
    yesterday = demo_runtime.clock().astimezone(tz).date() - timedelta(days=1)
    old = yesterday - timedelta(days=9)

    def make(day: date, source: str, kind: str) -> AccuracyDayRow:
        return AccuracyDayRow(source, kind, day, "0_6h", 120.0, 0.0, 6, NOW)  # type: ignore[arg-type]

    await timeseries.accuracy_days.put_many(
        [make(yesterday, "blended", "pv"), make(yesterday, "load_a", "load"), make(old, "x", "pv")]
    )
    rows, blended = await demo_runtime.recent_pv_accuracy()
    assert [(r.source_id, r.kind, r.day) for r in rows] == [("blended", "pv", yesterday)]
    assert blended == "blended"
    assert pv_forecast_error_w(rows, blended) == 120.0
    summary = await demo_runtime.today_summary()  # a row or None; never an exception
    assert summary is None or summary.day == demo_runtime.clock().astimezone(tz).date()


async def test_starting_the_notifications_wires_their_signals_to_the_event_buffer(
    demo_runtime: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    listeners: list[Any] = []

    class FakeService:
        def add_signal_listener(self, listener: Any) -> None:
            listeners.append(listener)

        async def start(self) -> None:
            return None

        async def stop(self) -> None:
            return None

    monkeypatch.setattr("suncore.app.runtime.build_notifications", lambda _rt: FakeService())
    buffer = SiteEventBuffer(demo_runtime.clock)
    monkeypatch.setattr(demo_runtime, "site_events", buffer)
    await demo_runtime._start_notifications()
    assert len(listeners) == 1
    listeners[0](SiteSignal("para14a_start"))
    assert [e.type for e in buffer.drain()] == ["para14a_start"]
    demo_runtime.notifications = None
