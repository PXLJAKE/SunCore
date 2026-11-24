# SPDX-License-Identifier: Apache-2.0
"""Backtest engine (Phase 5 WP6): execution model, overlay, replay of the demo history."""

from __future__ import annotations

import asyncio
import math
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from suncore.core.battery import BatteryModel, BatterySettings, BatteryStrategy
from suncore.core.control import BatteryBand
from suncore.core.params import REGISTRY, ParamError, check_whatif_overrides
from suncore.planner.inputs import PlannerInputs
from suncore.planner.run import PlannerRun
from suncore.sim.backtest import (
    MAX_REPLAN_BUDGET_S,
    BacktestSite,
    BacktestStoppedError,
    BatteryLimits,
    apply_overlay,
    default_solve,
    execute_slot,
    price_known_until,
    run_backtest,
)
from suncore.sim.backtest_models import (
    BacktestRequest,
    BacktestResult,
    BacktestWindowError,
    BatteryOverlay,
    TariffOverlay,
)
from suncore.sim.demo_history import (
    BATTERY_W,
    BATTERY_WH,
    EXPORT_PRICE,
    FORECAST_DAYS,
    PRICE_SOURCE,
    PV_SOURCES,
    seed_demo_history,
)
from suncore.storage import open_timeseries
from suncore.storage.timeseries import TimeseriesStore

BERLIN = ZoneInfo("Europe/Berlin")
NOW = datetime(2026, 11, 23, 9, 0, tzinfo=UTC)
TODAY = date(2026, 11, 23)
DEMO_DAYS = 45

REPRODUCTION_TOLERANCE = 0.05
"""Override-free replay of the demo's last 14 days vs the recorded cost of those days.

Measured 2026-11-24 on amd64: recorded 17.21 EUR, replay 17.41 EUR (+1.1 %). The demo's own
battery logic (self-consumption plus a fixed night grid charge before cloudy days) is not the
planner's, so the two cannot agree exactly; 5 % (about 4x the measured gap) is the tolerance in
this test and in the user guide."""


def demo_model(**over: Any) -> BatteryModel:
    fields: dict[str, Any] = {
        "capacity_wh": BATTERY_WH,
        "max_charge_w": BATTERY_W,
        "max_discharge_w": BATTERY_W,
        "reserve_pct": 10.0,
        "bms_drift_margin_pct": 0.0,
    }
    return BatteryModel(**{**fields, **over})


def demo_site(**over: Any) -> BacktestSite:
    fields: dict[str, Any] = {
        "tz": BERLIN,
        "tz_name": "Europe/Berlin",
        "battery": BatterySettings(model=demo_model()),
        "price_source_id": PRICE_SOURCE,
        "export_fallback": EXPORT_PRICE,
        "currency": "EUR",
        "pv_source_ids": tuple(s for s, _, _ in PV_SOURCES),
    }
    return BacktestSite(**{**fields, **over})


@pytest.fixture(scope="module")
def demo_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("backtest") / "t.db"

    async def seed() -> None:
        async with await open_timeseries(path) as db:
            await seed_demo_history(TimeseriesStore(db), now=NOW, days=DEMO_DAYS)

    asyncio.run(seed())
    return path


@pytest.fixture
async def store(demo_db: Path) -> AsyncIterator[TimeseriesStore]:
    async with await open_timeseries(demo_db) as db:
        yield TimeseriesStore(db)


def stable(result: BacktestResult) -> dict[str, Any]:
    """Everything but the wall-clock solve statistics."""
    return result.model_dump(mode="json", exclude={"stats"})


# --- execution model ------------------------------------------------------------------------

powers = st.floats(min_value=0, max_value=20_000, allow_nan=False)
socs = st.floats(min_value=0, max_value=100, allow_nan=False)


@st.composite
def bands(draw: st.DrawFn) -> BatteryBand | None:
    if draw(st.booleans()):
        return None
    lo = draw(st.floats(min_value=0, max_value=60))
    hi = draw(st.floats(min_value=lo, max_value=100))
    return BatteryBand(
        min_power_w=draw(st.sampled_from([None, 0.0, -2000.0, -8000.0])),
        max_power_w=draw(st.sampled_from([None, 0.0, 1500.0, 8000.0])),
        min_soc_pct=lo,
        max_soc_pct=hi,
        charge_from_surplus=draw(st.booleans()),
        grid_charge_max_w=draw(st.sampled_from([None, 1000.0, 3000.0])),
        discharge_lock=draw(st.booleans()),
    )


@settings(max_examples=300, deadline=None)
@given(
    pv=powers,
    house=powers,
    soc=socs,
    band=bands(),
    planned=st.floats(min_value=-8000, max_value=8000, allow_nan=False),
    export_limit=st.sampled_from([None, 0.0, 3000.0]),
    eta=st.floats(min_value=0.8, max_value=1.0),
)
def test_execution_conserves_energy_and_respects_limits(
    *,
    pv: float,
    house: float,
    soc: float,
    band: BatteryBand | None,
    planned: float,
    export_limit: float | None,
    eta: float,
) -> None:
    model = demo_model(charge_efficiency=eta, discharge_efficiency=eta)
    limits = BatteryLimits(
        model=model,
        floor_wh=model.soc_to_wh(10),
        ceiling_wh=model.soc_to_wh(100),
        max_charge_w=BATTERY_W,
        max_discharge_w=BATTERY_W,
    )
    energy = model.soc_to_wh(soc)
    done = execute_slot(
        pv,
        house,
        energy,
        0.25,
        limits=limits,
        band=band,
        planned_battery_w=planned,
        export_limit_w=export_limit,
    )
    # Balance: PV used + grid = house + battery, and curtailment only cuts PV.
    assert math.isclose(done.pv_w + done.grid_w, done.house_w + done.battery_w, abs_tol=1e-6)
    assert 0 <= done.curtail_w <= pv + 1e-9
    assert done.pv_w >= -1e-9
    if export_limit is not None:
        assert done.grid_w >= -export_limit - 1e-6
    # Battery power and content stay inside the limits (or where they started).
    assert -BATTERY_W - 1e-6 <= done.battery_w <= BATTERY_W + 1e-6
    lo = min(model.soc_to_wh(10), energy)
    hi = max(model.soc_to_wh(100), energy)
    assert lo - 1e-6 <= done.energy_wh <= hi + 1e-6
    # Energy bookkeeping with the efficiencies.
    if done.battery_w >= 0:
        expected = energy + 0.25 * eta * done.battery_w
    else:
        expected = energy + 0.25 * done.battery_w / eta
    assert math.isclose(done.energy_wh, expected, abs_tol=1e-6)
    if band is not None and band.discharge_lock:
        assert done.battery_w >= -1e-9
    if band is None or not band.grid_charge_max_w or planned <= 0:
        # Without planned grid charging the battery never draws from the grid.
        assert done.battery_w <= max(0.0, pv - house) + 1e-6


def test_execution_without_battery_is_the_pv_only_baseline() -> None:
    done = execute_slot(3000, 1000, 0.0, 0.25, limits=None, band=None)
    assert (done.battery_w, done.grid_w) == (0.0, -2000.0)


def test_prices_are_known_until_the_day_ahead_publication() -> None:
    morning = datetime(2026, 11, 10, 8, 0, tzinfo=BERLIN)
    afternoon = datetime(2026, 11, 10, 14, 0, tzinfo=BERLIN)
    assert price_known_until(morning, BERLIN) == datetime(2026, 11, 11, tzinfo=BERLIN)
    assert price_known_until(afternoon, BERLIN) == datetime(2026, 11, 12, tzinfo=BERLIN)


# --- overlay validation ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "key",
    [
        "controller.reserve_w",  # safety
        "controller.default_max_switches_per_hour",  # safety
        "controller.tick_s",  # controller, not safety
        "planner.min_event_gap_s",  # planner, but a runtime cadence (no whatif tag)
        "tariff.export.remunerate_negative",
    ],
)
def test_overlay_rejects_parameters_a_backtest_may_not_change(key: str) -> None:
    with pytest.raises(ParamError) as caught:
        check_whatif_overrides({key: 1})
    assert caught.value.code == "param_not_whatif"
    with pytest.raises(ValidationError) as invalid:
        BacktestRequest(start=date(2026, 11, 1), end=date(2026, 11, 2), params={key: 1})
    assert invalid.value.errors()[0]["type"] == "suncore.param_not_whatif"


@settings(max_examples=50, deadline=None)
@given(key=st.sampled_from(sorted(REGISTRY)))
def test_no_safety_parameter_passes_the_overlay(key: str) -> None:
    parameter = REGISTRY[key]
    allowed = (
        not parameter.safety
        and parameter.group in ("planner", "forecast")
        and "whatif" in parameter.tags
    )
    if allowed:
        assert check_whatif_overrides({key: parameter.default}) == {key: parameter.default}
    else:
        with pytest.raises(ParamError, match="param_not_whatif"):
            check_whatif_overrides({key: parameter.default})


def test_overlay_rejects_unknown_keys_and_out_of_range_values() -> None:
    with pytest.raises(ValidationError) as unknown:
        BacktestRequest(start=date(2026, 11, 1), end=date(2026, 11, 1), params={"nope": 1})
    assert unknown.value.errors()[0]["type"] == "suncore.param_unknown"
    with pytest.raises(ValidationError) as out_of_range:
        BacktestRequest(
            start=date(2026, 11, 1), end=date(2026, 11, 1), params={"planner.budget_s": 500}
        )
    assert out_of_range.value.errors()[0]["type"] == "suncore.param_out_of_range"


def test_window_rules() -> None:
    with pytest.raises(ValidationError) as order:
        BacktestRequest(start=date(2026, 11, 2), end=date(2026, 11, 1))
    assert order.value.errors()[0]["type"] == "suncore.backtest_window_order"
    with pytest.raises(ValidationError) as long:
        BacktestRequest(start=date(2026, 10, 1), end=date(2026, 11, 1))
    assert long.value.errors()[0]["type"] == "suncore.backtest_window_too_long"
    assert BacktestRequest(start=date(2026, 10, 3), end=date(2026, 11, 1)).days == 30


def test_apply_overlay_changes_only_the_copy() -> None:
    site = demo_site()
    request = BacktestRequest(
        start=date(2026, 11, 1),
        end=date(2026, 11, 1),
        params={"planner.budget_s": 120.0, "planner.mip_gap": 0.05},
        battery=BatteryOverlay(
            strategy=BatteryStrategy.PREFER_FULL, capacity_wh=20_000, reserve_pct=20
        ),
    )
    changed = apply_overlay(site, request)
    assert changed.budget_s == MAX_REPLAN_BUDGET_S  # capped for the Pi
    assert changed.mip_gap == 0.05
    assert changed.battery.strategy is BatteryStrategy.PREFER_FULL
    assert changed.battery.model is not None
    assert (changed.battery.model.capacity_wh, changed.battery.model.reserve_pct) == (20_000, 20)
    assert site.battery.model is not None
    assert site.battery.model.capacity_wh == BATTERY_WH  # the input is untouched


# --- replay of the demo history ------------------------------------------------------------


async def test_window_must_be_in_the_past(store: TimeseriesStore) -> None:
    request = BacktestRequest(start=date(2026, 11, 22), end=TODAY)
    with pytest.raises(BacktestWindowError):
        await run_backtest(store, demo_site(), request, today=TODAY)


async def test_actual_matches_the_recorded_day_summary(store: TimeseriesStore) -> None:
    request = BacktestRequest(start=date(2026, 11, 20), end=date(2026, 11, 22))
    result = await run_backtest(store, demo_site(), request, today=TODAY)
    assert not result.skipped
    assert [d.day for d in result.days] == [date(2026, 11, d) for d in (20, 21, 22)]
    for day in result.days:
        row = await store.day_summaries.get(day.day)
        assert row is not None
        assert row.cost_net is not None
        assert day.actual.cost_net == pytest.approx(row.cost_net, abs=1e-5)
        assert day.baseline_pv_only_cost == pytest.approx(row.baseline_pv_only_cost, abs=1e-5)
        assert day.baseline_no_pv_cost == pytest.approx(row.baseline_no_pv_cost, abs=1e-5)
        # Same PV, same house load in every run; only the battery and the grid differ.
        assert day.whatif.pv_wh == pytest.approx(day.actual.pv_wh, abs=0.5)
        assert day.whatif.house_wh == pytest.approx(day.actual.house_wh, abs=0.5)
        assert day.priced_slots == day.slots == 96
        # Energy balance of the replay over the day: PV + import = house + charge - discharge
        # + export (Wh, rounded to 0.1 per figure).
        w = day.whatif
        assert w.pv_wh + w.import_wh + w.discharge_wh == pytest.approx(
            w.house_wh + w.charge_wh + w.export_wh, abs=1.0
        )
    assert result.totals is not None
    assert result.totals.days == 3
    assert result.stats.replans == 72
    # Budget (plan §0 item 7: one hourly replan <= 10 s on a Pi 4); CI on amd64 checks 3 s,
    # measured about 0.05 s (scripts/bench_backtest.py).
    assert result.stats.solve_s_max < 3.0
    assert result.label == "plan_with_perfect_execution"
    assert {r.key for r in result.limitations} >= {"backtest.limit.perfect_execution"}
    assert len(result.hourly.starts) == 72


async def test_same_input_gives_the_same_result(store: TimeseriesStore) -> None:
    request = BacktestRequest(
        start=date(2026, 11, 18), end=date(2026, 11, 19), perfect_foresight=True
    )
    first = await run_backtest(store, demo_site(), request, today=TODAY)
    second = await run_backtest(store, demo_site(), request, today=TODAY)
    assert stable(first) == stable(second)


async def test_days_without_forecasts_are_skipped_and_reported(store: TimeseriesStore) -> None:
    # The demo stores forecasts for its last FORECAST_DAYS days only.
    last_without = TODAY - timedelta(days=FORECAST_DAYS + 1)
    request = BacktestRequest(
        start=last_without - timedelta(days=1), end=last_without + timedelta(days=1)
    )
    result = await run_backtest(store, demo_site(), request, today=TODAY)
    assert [(s.day, s.reason.key) for s in result.skipped] == [
        (last_without - timedelta(days=1), "backtest.skip.no_forecast"),
        (last_without, "backtest.skip.no_forecast"),
    ]
    assert [d.day for d in result.days] == [last_without + timedelta(days=1)]


async def test_days_without_any_data_are_skipped(store: TimeseriesStore) -> None:
    first = TODAY - timedelta(days=DEMO_DAYS + 5)
    request = BacktestRequest(start=first, end=first + timedelta(days=1))
    result = await run_backtest(store, demo_site(), request, today=TODAY)
    assert result.totals is None
    assert {s.reason.key for s in result.skipped} == {"backtest.skip.incomplete_measurements"}


async def test_flat_tariff_overlay_prices_every_run_alike(store: TimeseriesStore) -> None:
    request = BacktestRequest(
        start=date(2026, 11, 21),
        end=date(2026, 11, 21),
        tariff=TariffOverlay(import_price=0.30, export_price=0.0),
    )
    result = await run_backtest(store, demo_site(), request, today=TODAY)
    day = result.days[0]
    assert result.tariff == "fixed"
    assert day.whatif.export_revenue == 0.0
    assert day.whatif.cost_net == pytest.approx(day.whatif.import_wh / 1000 * 0.30, abs=1e-5)
    assert day.baseline_no_pv_cost == pytest.approx(day.actual.house_wh / 1000 * 0.30, abs=1e-4)
    # Export pays nothing and grid charging is off: the replay never costs more than PV alone.
    assert day.whatif.cost_net <= day.baseline_pv_only_cost + 1e-6
    assert "backtest.limit.prices_day_ahead" not in {r.key for r in result.limitations}


async def test_perfect_foresight_with_a_huge_battery_beats_the_baseline(
    store: TimeseriesStore,
) -> None:
    site = demo_site(
        battery=BatterySettings(
            model=demo_model(capacity_wh=200_000, max_charge_w=50_000, max_discharge_w=50_000)
        )
    )
    request = BacktestRequest(
        start=date(2026, 11, 20),
        end=date(2026, 11, 21),
        perfect_foresight=True,
        tariff=TariffOverlay(import_price=0.30, export_price=0.0),
    )
    result = await run_backtest(store, site, request, today=TODAY)
    assert result.totals is not None
    assert result.totals.perfect is not None
    assert result.totals.perfect.cost_net <= result.totals.baseline_pv_only_cost + 1e-6
    assert result.hourly.perfect_soc_pct is not None


async def test_stop_is_checked_before_every_replan(store: TimeseriesStore) -> None:
    calls = 0

    def solve(inputs: PlannerInputs, budget: float, gap: float, prev: Any) -> PlannerRun:
        nonlocal calls
        calls += 1
        return default_solve(inputs, budget, gap, prev)

    request = BacktestRequest(start=date(2026, 11, 20), end=date(2026, 11, 22))
    with pytest.raises(BacktestStoppedError):
        await run_backtest(
            store, demo_site(), request, today=TODAY, solve=solve, should_stop=lambda: calls >= 5
        )
    assert calls == 5


async def test_progress_is_reported_per_day(store: TimeseriesStore) -> None:
    seen: list[tuple[int, int]] = []
    request = BacktestRequest(start=date(2026, 11, 21), end=date(2026, 11, 22))
    await run_backtest(
        store, demo_site(), request, today=TODAY, progress=lambda d, t: seen.append((d, t))
    )
    assert seen == [(1, 2), (2, 2)]


@pytest.mark.slow
async def test_override_free_replay_reproduces_the_recorded_cost(store: TimeseriesStore) -> None:
    request = BacktestRequest(start=date(2026, 11, 9), end=date(2026, 11, 22))
    result = await run_backtest(store, demo_site(), request, today=TODAY)
    assert result.totals is not None
    assert result.totals.days == 14
    recorded = sum(
        r.cost_net or 0.0 for r in await store.day_summaries.between(request.start, request.end)
    )
    assert result.totals.actual.cost_net == pytest.approx(recorded, abs=1e-4)
    gap = abs(result.totals.whatif.cost_net - recorded) / recorded
    assert gap <= REPRODUCTION_TOLERANCE
