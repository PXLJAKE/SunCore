# SPDX-License-Identifier: Apache-2.0
"""``/api/v1/history/*``, ``/plan/at``, ``/plan/compare``, decision filters and explain (WP4)."""

from __future__ import annotations

import asyncio
import csv
import dataclasses
import io
import time
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest

from suncore.api import HistoryContext, HistorySources, create_app
from suncore.app.summaries import DaySummaryJob, SummaryContext
from suncore.core.decisions import (
    Decision,
    DecisionKind,
    Intent,
    OperatingMode,
    Origin,
    Reason,
    SwitchAction,
)
from suncore.sim.demo_history import (
    PUMP_ID,
    seed_demo_decisions,
    seed_demo_history,
    seed_demo_plans,
)
from suncore.storage import Database, open_timeseries
from suncore.storage.decisions import DecisionLog
from suncore.storage.plans import PlanSnapshotStore
from suncore.storage.timeseries import TimeseriesStore
from tests.api.conftest import FakeServices, client_for

NOW = datetime(2026, 11, 23, 9, 0, tzinfo=UTC)
TZ = ZoneInfo("Europe/Berlin")
DAYS = 40


async def _seed(root: Path) -> None:
    async with await open_timeseries(root / "ts.db") as db:
        await seed_demo_history(TimeseriesStore(db), now=NOW, days=DAYS)
    async with await Database.open(root / "plans.db") as db:
        await seed_demo_plans(PlanSnapshotStore(db), now=NOW, count=2)


@pytest.fixture(scope="module")
def seeded(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Forty demo days and two plan snapshots, seeded once (read only in the tests)."""
    root = tmp_path_factory.mktemp("history")
    asyncio.run(_seed(root))
    return root


def _context() -> HistoryContext:
    return HistoryContext(
        tz=TZ, tz_name="Europe/Berlin", currency="EUR", devices={PUMP_ID: "Pool pump"}
    )


@pytest.fixture
async def api(
    seeded: Path, services: FakeServices, tmp_path: Path
) -> AsyncIterator[httpx.AsyncClient]:
    ts_db = await open_timeseries(seeded / "ts.db")
    plans_db = await Database.open(seeded / "plans.db")
    dec_db = await Database.open(tmp_path / "dec.db")
    store = TimeseriesStore(ts_db)
    log = DecisionLog(dec_db)
    await seed_demo_decisions(log, now=NOW, days=14)
    job = DaySummaryJob(store, lambda: NOW)
    summary_ctx = SummaryContext(tz=TZ, tz_name="Europe/Berlin", currency="EUR")

    async def live_day(day: date) -> Any:
        return await job.preview(day, summary_ctx)

    services.history = HistorySources(
        decision_log=log,
        timeseries=store,
        plans=PlanSnapshotStore(plans_db),
        context=_context(),
        clock=lambda: NOW,
        live_day=live_day,
    )
    async with client_for(create_app(services)) as client:
        yield client
    for db in (ts_db, plans_db, dec_db):
        await db.close()


def parse_csv(text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(text)))


# --- summary ---------------------------------------------------------------------------------


async def test_day_summary_is_the_frozen_row(api: httpx.AsyncClient) -> None:
    r = await api.get("/api/v1/history/summary", params={"period": "day", "date": "2026-11-20"})
    assert r.status_code == 200
    body = r.json()
    assert body["key"] == "2026-11-20"
    assert body["complete"] is True
    assert body["in_progress"] is False
    assert body["includes_live"] is False
    assert body["energy"]["pv_wh"] > 0
    assert 0 <= body["kpis"]["autarky"] <= 1
    assert body["cost"]["currency"] == "EUR"
    assert body["cost"]["savings_vs_no_pv"] == pytest.approx(
        body["cost"]["baseline_no_pv_cost"] - body["cost"]["cost_net"]
    )
    assert len(body["buckets"]) == 1


async def test_week_sums_the_days(api: httpx.AsyncClient) -> None:
    r = await api.get("/api/v1/history/summary", params={"period": "week", "date": "2026-11-18"})
    body = r.json()
    assert body["key"] == "2026-W47"
    assert body["days"] == 7
    assert body["days_with_data"] == 7
    assert len(body["buckets"]) == 7
    assert body["energy"]["pv_wh"] == pytest.approx(
        sum(b["energy"]["pv_wh"] for b in body["buckets"])
    )
    assert body["cost"]["cost_net"] == pytest.approx(
        sum(b["cost"]["cost_net"] for b in body["buckets"])
    )
    flows = body["flows"]
    assert body["energy"]["import_wh"] == pytest.approx(
        flows["grid_to_house_wh"] + flows["grid_to_battery_wh"]
    )


async def test_month_with_the_dst_switch_has_a_25_hour_day(api: httpx.AsyncClient) -> None:
    r = await api.get("/api/v1/history/summary", params={"period": "month", "date": "2026-10-28"})
    body = r.json()
    assert body["key"] == "2026-10"
    assert body["days"] == 31
    day = await api.get("/api/v1/history/summary", params={"period": "day", "date": "2026-10-25"})
    start = datetime.fromisoformat(day.json()["start"])
    end = datetime.fromisoformat(day.json()["end"])
    assert end - start == timedelta(hours=25)
    assert day.json()["complete"] is True
    assert start.utcoffset() == timedelta(hours=2)


async def test_year_has_one_bar_per_month_until_now(api: httpx.AsyncClient) -> None:
    body = (await api.get("/api/v1/history/summary", params={"period": "year"})).json()
    assert body["key"] == "2026"
    assert body["in_progress"] is True
    assert [b["key"] for b in body["buckets"]][-2:] == ["2026-10", "2026-11"]
    assert len(body["buckets"]) == 11
    empty = body["buckets"][0]
    assert empty["days_with_data"] == 0
    assert empty["complete"] is False
    assert body["complete"] is False


async def test_the_current_day_is_computed_live_and_is_incomplete(api: httpx.AsyncClient) -> None:
    body = (await api.get("/api/v1/history/summary", params={"period": "day"})).json()
    assert body["key"] == "2026-11-23"  # default date: today in site time
    assert body["in_progress"] is True
    assert body["complete"] is False


async def test_summary_csv_is_utf8_comma_dot_and_iso_time(api: httpx.AsyncClient) -> None:
    r = await api.get(
        "/api/v1/history/summary",
        params={"period": "week", "date": "2026-11-18", "format": "csv"},
    )
    assert r.status_code == 200
    assert r.headers["content-type"] == "text/csv; charset=utf-8"
    assert "attachment" in r.headers["content-disposition"]
    rows = parse_csv(r.content.decode("utf-8"))
    assert len(rows) == 7
    assert rows[0]["bucket"] == "2026-11-16"
    assert rows[0]["start"].endswith("+01:00")
    assert "." in rows[0]["pv_wh"]
    assert float(rows[0]["pv_wh"]) > 0
    assert rows[0]["currency"] == "EUR"


async def test_summary_validation(api: httpx.AsyncClient) -> None:
    for params in ({"period": "decade"}, {"date": "not-a-date"}, {"format": "xml"}):
        r = await api.get("/api/v1/history/summary", params=params)
        assert r.status_code == 422
        assert r.json()["detail"] == "api.error.validation"


async def test_empty_history_gives_zeros_and_nulls(services: FakeServices, tmp_path: Path) -> None:
    async with (
        await open_timeseries(tmp_path / "e.db") as ts_db,
        await Database.open(tmp_path / "e2.db") as db,
    ):
        services.history = HistorySources(
            decision_log=DecisionLog(db),
            timeseries=TimeseriesStore(ts_db),
            plans=PlanSnapshotStore(db),
            context=_context(),
            clock=lambda: NOW,
        )
        async with client_for(create_app(services)) as client:
            body = (await client.get("/api/v1/history/summary", params={"period": "month"})).json()
            assert body["days_with_data"] == 0
            assert body["energy"]["pv_wh"] == 0
            assert body["kpis"]["autarky"] is None
            assert body["cost"]["cost_net"] is None
            assert body["coverage"] is None
            for path in ("sankey", "devices", "cycles", "forecast-skill", "heatmap"):
                assert (await client.get(f"/api/v1/history/{path}")).status_code == 200
            assert (
                await client.get("/api/v1/plan/at", params={"ts": NOW.isoformat()})
            ).status_code == 404


async def test_no_time_series_database_is_a_catalogue_error(services: FakeServices) -> None:
    async with client_for(create_app(services)) as client:
        r = await client.get("/api/v1/history/summary")
    assert r.status_code == 503
    assert r.json()["detail"] == "api.error.history_unavailable"


# --- sankey, cycles, skill, devices, heatmap ---------------------------------------------------


async def test_sankey_links_are_the_summary_flows(api: httpx.AsyncClient) -> None:
    params = {"period": "week", "date": "2026-11-18"}
    flows = (await api.get("/api/v1/history/summary", params=params)).json()["flows"]
    body = (await api.get("/api/v1/history/sankey", params=params)).json()
    assert len(body["links"]) == 7
    by = {(link["source"], link["target"]): link["wh"] for link in body["links"]}
    assert by[("pv", "house")] == pytest.approx(flows["pv_to_house_wh"])
    assert by[("grid_import", "battery_charge")] == pytest.approx(flows["grid_to_battery_wh"])
    csv_rows = parse_csv(
        (await api.get("/api/v1/history/sankey", params={**params, "format": "csv"})).text
    )
    assert len(csv_rows) == 7


async def test_cycles_per_day_with_the_soc_range(api: httpx.AsyncClient) -> None:
    r = await api.get("/api/v1/history/cycles", params={"from": "2026-11-10", "to": "2026-11-20"})
    body = r.json()
    assert len(body["days"]) == 11
    assert all(d["soc_min_pct"] <= d["soc_max_pct"] for d in body["days"])
    assert body["total_equivalent_full_cycles"] == pytest.approx(
        sum(d["equivalent_full_cycles"] for d in body["days"])
    )
    bad = await api.get("/api/v1/history/cycles", params={"from": "2026-11-20", "to": "2026-11-10"})
    assert bad.status_code == 422
    assert bad.json()["detail"] == "api.error.history_range_invalid"
    far = await api.get("/api/v1/history/cycles", params={"from": "2020-01-01", "to": "2026-11-10"})
    assert far.status_code == 422


async def test_forecast_skill_groups_by_source_and_horizon(api: httpx.AsyncClient) -> None:
    body = (await api.get("/api/v1/history/forecast-skill")).json()
    assert body["series"]
    first = body["series"][0]
    assert first["n"] == sum(p["n"] for p in first["points"])
    assert first["mae_w"] is not None
    only = (
        await api.get("/api/v1/history/forecast-skill", params={"source": first["source_id"]})
    ).json()
    assert {s["source_id"] for s in only["series"]} == {first["source_id"]}
    csv_rows = parse_csv(
        (await api.get("/api/v1/history/forecast-skill", params={"format": "csv"})).text
    )
    assert {"source", "horizon", "mae_w", "bias_w", "n"} <= set(csv_rows[0])


async def test_devices_run_time_and_starts(api: httpx.AsyncClient) -> None:
    body = (
        await api.get("/api/v1/history/devices", params={"period": "month", "date": "2026-11-10"})
    ).json()
    pump = body["devices"][0]
    assert pump["device_id"] == PUMP_ID
    assert pump["name"] == "Pool pump"
    assert pump["run_hours"] > 0
    assert pump["starts"] >= 1
    assert len(pump["days"]) == 23  # up to today
    csv_rows = parse_csv(
        (
            await api.get(
                "/api/v1/history/devices",
                params={"period": "week", "date": "2026-11-18", "format": "csv"},
            )
        ).text
    )
    assert len(csv_rows) == 7


async def test_heatmap_is_weekday_by_hour(api: httpx.AsyncClient) -> None:
    body = (await api.get("/api/v1/history/heatmap")).json()
    assert len(body["values"]) == 7
    assert all(len(row) == 24 for row in body["values"])
    assert body["key"] == "site.house_w"
    assert sum(sum(row) for row in body["counts"]) > 0
    csv_rows = parse_csv((await api.get("/api/v1/history/heatmap", params={"format": "csv"})).text)
    assert len(csv_rows) == 168
    assert (await api.get("/api/v1/history/heatmap", params={"key": "x y"})).status_code == 422


# --- series ----------------------------------------------------------------------------------


def series_params(**over: Any) -> dict[str, Any]:
    return {"key": ["site.pv_w", "site.grid_w"], **over}


async def test_resolution_follows_the_span_and_the_point_cap(api: httpx.AsyncClient) -> None:
    async def pick(hours: float) -> dict[str, Any]:
        end = NOW - timedelta(days=1)
        r = await api.get(
            "/api/v1/history/series",
            params=series_params(
                **{"from": (end - timedelta(hours=hours)).isoformat(), "to": end.isoformat()}
            ),
        )
        assert r.status_code == 200
        return r.json()

    six_hours = await pick(6)
    assert six_hours["resolution"] == "1m"
    assert six_hours["resolution_reason"] == "span"
    assert len(six_hours["times"]) <= 2000
    three_days = await pick(72)
    assert three_days["resolution"] == "15m"
    assert len(three_days["times"]) == 288 + 1 or len(three_days["times"]) == 288
    month = await pick(24 * 25)
    assert month["resolution"] == "1h"
    assert month["resolution_reason"] == "points"
    assert len(month["times"]) <= 2000
    long = await pick(24 * 36)
    assert long["resolution"] == "1d"
    assert all(len(s["values"]) == len(long["times"]) for s in long["series"])
    assert {s["key"]: s["unit"] for s in long["series"]}["site.pv_w"] == "W"


async def test_daily_series_follow_local_days_across_the_dst_switch(api: httpx.AsyncClient) -> None:
    r = await api.get(
        "/api/v1/history/series",
        params=series_params(
            **{
                "from": "2026-10-20T00:00:00+02:00",
                "to": "2026-11-05T00:00:00+01:00",
                "resolution": "1d",
            }
        ),
    )
    body = r.json()
    offsets = {t[-6:] for t in body["times"]}
    assert offsets == {"+02:00", "+01:00"}
    assert body["times"][5].startswith("2026-10-25T00:00:00+02:00")
    assert body["times"][6].startswith("2026-10-26T00:00:00+01:00")
    assert any(v is not None for v in body["series"][0]["values"])


async def test_series_csv(api: httpx.AsyncClient) -> None:
    r = await api.get(
        "/api/v1/history/series",
        params=series_params(
            **{
                "from": "2026-11-20T00:00:00",
                "to": "2026-11-21T00:00:00",
                "resolution": "15m",
                "format": "csv",
            }
        ),
    )
    rows = parse_csv(r.text)
    assert list(rows[0]) == ["time", "site.pv_w_W", "site.grid_w_W"]
    assert rows[0]["time"] == "2026-11-20T00:00:00+01:00"  # naive input is site time
    assert len(rows) == 96 or len(rows) == 97


async def test_series_bounds_and_validation(api: httpx.AsyncClient) -> None:
    async def get(**params: Any) -> httpx.Response:
        return await api.get("/api/v1/history/series", params=params)

    r = await get(key="site.nope")
    assert r.status_code == 422
    assert r.json()["detail"] == "api.error.history_key_invalid"
    assert (await get(key="device.a/b.on")).status_code == 422
    assert (await get(key=[f"device.d{i}.on" for i in range(9)])).status_code == 422
    assert (await get(key=["site.pv_w", "site.pv_w"])).json()[
        "detail"
    ] == "api.error.history_key_invalid"
    assert (await api.get("/api/v1/history/series")).status_code == 422  # key is required
    r = await get(key="site.pv_w", **{"from": "2026-11-20T00:00:00Z", "to": "2026-11-19T00:00:00Z"})
    assert r.json()["detail"] == "api.error.history_range_invalid"
    r = await get(key="site.pv_w", **{"from": "2025-01-01T00:00:00Z", "to": "2026-11-19T00:00:00Z"})
    assert r.json()["detail"] == "api.error.history_range_invalid"  # longer than 400 days
    r = await get(
        key="site.pv_w",
        resolution="1m",
        **{"from": "2026-11-01T00:00:00Z", "to": "2026-11-20T00:00:00Z"},
    )
    assert r.json()["detail"] == "api.error.history_range_invalid"  # finer than the span allows


# --- plan at / compare -----------------------------------------------------------------------


async def test_plan_at_returns_the_snapshot_valid_then(api: httpx.AsyncClient) -> None:
    r = await api.get("/api/v1/plan/at", params={"ts": "2026-11-22T12:00:00+00:00"})
    assert r.status_code == 200
    body = r.json()
    assert body["created_at"].startswith("2026-11-22T10:00:00")
    assert body["interval_index"] is not None
    assert body["plan"]["intervals"]
    older = await api.get("/api/v1/plan/at", params={"ts": "2026-11-21T10:30:00Z"})
    assert older.json()["snapshot_id"] != body["snapshot_id"]
    csv_rows = parse_csv(
        (
            await api.get("/api/v1/plan/at", params={"ts": "2026-11-22T12:00:00Z", "format": "csv"})
        ).text
    )
    assert len(csv_rows) == len(body["plan"]["intervals"])


async def test_plan_at_is_404_when_nothing_was_stored(api: httpx.AsyncClient) -> None:
    r = await api.get("/api/v1/plan/at", params={"ts": "2026-11-01T00:00:00Z"})
    assert r.status_code == 404
    assert r.json()["detail"] == "api.error.snapshot_not_found"
    assert (await api.get("/api/v1/plan/at")).status_code == 422
    assert (await api.get("/api/v1/plan/at", params={"ts": "yesterday"})).status_code == 422


async def test_plan_compare_lists_the_interval_differences(api: httpx.AsyncClient) -> None:
    a = (await api.get("/api/v1/plan/at", params={"ts": "2026-11-21T12:00:00Z"})).json()
    b = (await api.get("/api/v1/plan/at", params={"ts": "2026-11-22T12:00:00Z"})).json()
    r = await api.get("/api/v1/plan/compare", params={"a": a["snapshot_id"], "b": b["snapshot_id"]})
    assert r.status_code == 200
    body = r.json()
    assert body["a"]["snapshot_id"] == a["snapshot_id"]
    assert body["changed"] == len(body["intervals"]) > 0
    assert all(d["only_in"] in ("a", "b") or (d["a"] and d["b"]) for d in body["intervals"])
    by_time = await api.get(
        "/api/v1/plan/compare", params={"a": "2026-11-21T12:00:00Z", "b": "2026-11-22T12:00:00Z"}
    )
    assert by_time.json()["a"]["snapshot_id"] == a["snapshot_id"]
    same = await api.get(
        "/api/v1/plan/compare", params={"a": a["snapshot_id"], "b": a["snapshot_id"]}
    )
    assert same.json()["changed"] == 0
    csv_rows = parse_csv(
        (
            await api.get(
                "/api/v1/plan/compare",
                params={"a": a["snapshot_id"], "b": b["snapshot_id"], "format": "csv"},
            )
        ).text
    )
    assert len(csv_rows) == body["changed"]


async def test_plan_compare_errors(api: httpx.AsyncClient) -> None:
    r = await api.get("/api/v1/plan/compare", params={"a": "999", "b": "1"})
    assert r.status_code == 404
    assert r.json()["detail"] == "api.error.snapshot_not_found"
    r = await api.get("/api/v1/plan/compare", params={"a": "soon", "b": "1"})
    assert r.status_code == 422
    assert r.json()["detail"] == "api.error.history_range_invalid"
    assert (await api.get("/api/v1/plan/compare", params={"a": "1"})).status_code == 422


# --- decisions -------------------------------------------------------------------------------


async def test_decision_filters_and_paging(api: httpx.AsyncClient) -> None:
    everything = await api.get("/api/v1/decisions", params={"limit": 1000})
    total = int(everything.headers["x-total-count"])
    assert total == len(everything.json()) > 4
    ids = everything.headers["x-decision-ids"].split(",")
    assert len(ids) == total
    assert ids == sorted(ids, key=int, reverse=True)  # newest first

    page = await api.get("/api/v1/decisions", params={"limit": 2, "offset": 2})
    assert [d["intent"]["id"] for d in page.json()] == [
        d["intent"]["id"] for d in everything.json()[2:4]
    ]
    assert page.headers["x-total-count"] == str(total)

    layer = await api.get("/api/v1/decisions", params={"layer": "controller"})
    assert len(layer.json()) == total
    assert (await api.get("/api/v1/decisions", params={"layer": "planner"})).json() == []
    assert (await api.get("/api/v1/decisions", params={"layer": "robot"})).status_code == 422

    on = await api.get("/api/v1/decisions", params={"reason": "controller.surplus_on"})
    assert 0 < len(on.json()) < total
    prefix = await api.get("/api/v1/decisions", params={"reason": "controller"})
    assert len(prefix.json()) == total
    partial = await api.get("/api/v1/decisions", params={"reason": "controller.surplus"})
    assert partial.json() == []  # prefixes end at a dot
    via_decision = await api.get("/api/v1/decisions", params={"reason": "safety.shadow_mode"})
    assert len(via_decision.json()) == total

    text = await api.get("/api/v1/decisions", params={"q": "SURPLUS_ON"})
    assert len(text.json()) == len(on.json())
    assert (await api.get("/api/v1/decisions", params={"q": "100%"})).json() == []
    assert (await api.get("/api/v1/decisions", params={"device_id": "nope"})).json() == []

    newest = datetime.fromisoformat(everything.json()[0]["ts"])
    window = await api.get(
        "/api/v1/decisions",
        params={"from": (newest - timedelta(days=1)).isoformat(), "to": newest.isoformat()},
    )
    assert 0 < len(window.json()) < total  # "to" is exclusive
    assert all(datetime.fromisoformat(d["ts"]) < newest for d in window.json())

    kind = await api.get("/api/v1/decisions", params={"kind": "accepted"})
    assert kind.json() == []
    assert kind.headers["x-total-count"] == "0"


async def test_decision_bounds(api: httpx.AsyncClient) -> None:
    for params in ({"offset": -1}, {"limit": 0}, {"reason": "Bad Key"}, {"q": ""}, {"format": "x"}):
        assert (await api.get("/api/v1/decisions", params=params)).status_code == 422


async def test_decisions_csv(api: httpx.AsyncClient) -> None:
    r = await api.get("/api/v1/decisions", params={"format": "csv", "limit": 3})
    rows = parse_csv(r.text)
    assert len(rows) == 3
    assert rows[0]["device_id"] == PUMP_ID
    assert rows[0]["layer"] == "controller"
    assert rows[0]["time"].endswith(("+01:00", "+02:00"))
    assert "safety.shadow_mode" in rows[0]["reasons"]
    assert r.headers["x-total-count"]


async def test_decision_explain(api: httpx.AsyncClient, services: FakeServices) -> None:
    assert services.history is not None
    at = datetime(2026, 11, 22, 13, 0, tzinfo=UTC)
    intent = Intent(
        id="explain-me",
        ts=at,
        asset_id=PUMP_ID,
        action=SwitchAction(on=True),
        reasons=(Reason(key="controller.surplus_on", args={"need_w": 800}),),
        origin=Origin.CONTROLLER,
    )
    await services.history.decision_log.append(
        Decision(
            intent=intent,
            kind=DecisionKind.SHADOW,
            ts=at,
            reasons=(Reason(key="safety.shadow_mode"),),
            mode=OperatingMode.SHADOW,
        )
    )
    listing = await api.get("/api/v1/decisions", params={"q": "explain-me"})
    decision_id = int(listing.headers["x-decision-ids"])
    r = await api.get(f"/api/v1/decisions/{decision_id}/explain")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == decision_id
    assert body["decision"]["intent"]["id"] == "explain-me"
    assert body["before"], "the demo log has earlier decisions of the pump"
    assert all(e["id"] < decision_id for e in body["before"])
    assert body["after"] == []
    assert body["plan"]["created_at"].startswith("2026-11-22T10:00:00")
    assert body["plan"]["interval"] is not None
    inputs = {i["key"]: i for i in body["inputs"]}
    assert inputs["site.pv_w"]["value"] is not None
    assert inputs["site.soc_pct"]["unit"] == "%"
    assert body["inputs_resolution"] == "15m"
    assert body["inputs_at"].startswith("2026-11-22T13:00:00")


async def test_explain_errors(api: httpx.AsyncClient) -> None:
    r = await api.get("/api/v1/decisions/999999/explain")
    assert r.status_code == 404
    assert r.json()["detail"] == "api.error.decision_not_found"
    assert (await api.get("/api/v1/decisions/0/explain")).status_code == 422
    assert (await api.get("/api/v1/decisions/abc/explain")).status_code == 422


async def test_filters_without_the_database_are_unavailable(services: FakeServices) -> None:
    async with client_for(create_app(services)) as client:
        assert (await client.get("/api/v1/decisions")).status_code == 200  # legacy path
        r = await client.get("/api/v1/decisions", params={"q": "x"})
    assert r.status_code == 503
    assert r.json()["detail"] == "api.error.history_unavailable"


# --- performance -----------------------------------------------------------------------------


async def test_a_year_of_day_summaries_reads_fast(services: FakeServices, tmp_path: Path) -> None:
    async with (
        await open_timeseries(tmp_path / "y.db") as ts_db,
        await Database.open(tmp_path / "y2.db") as db,
    ):
        store = TimeseriesStore(ts_db)
        await seed_demo_history(store, now=NOW, days=3)
        template = (await store.day_summaries.between(date(2026, 11, 20), date(2026, 11, 20)))[0]
        first = date(2026, 1, 1)
        for i in range(327):
            day = first + timedelta(days=i)
            midnight = datetime.combine(day, datetime.min.time(), tzinfo=TZ).astimezone(UTC)
            await store.day_summaries.put(
                dataclasses.replace(
                    template, day=day, start=midnight, end=midnight + timedelta(hours=24)
                )
            )
        assert await store.day_summaries.count() >= 327
        services.history = HistorySources(
            decision_log=DecisionLog(db),
            timeseries=store,
            plans=PlanSnapshotStore(db),
            context=_context(),
            clock=lambda: NOW,
        )
        async with client_for(create_app(services)) as client:
            started = time.perf_counter()
            r = await client.get("/api/v1/history/summary", params={"period": "year"})
            elapsed = time.perf_counter() - started
    assert r.status_code == 200
    assert r.json()["days_with_data"] >= 327
    assert elapsed < 1.0  # decision 8: 300 ms target, 1 s CI budget
