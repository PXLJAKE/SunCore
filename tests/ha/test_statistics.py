# SPDX-License-Identifier: Apache-2.0
"""Quarter-hour history from ``recorder/statistics_during_period`` (Phase 4 WP3)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest

from suncore.core.series import SeriesUnit
from suncore.ha.connection import CommandError, NotConnectedError
from suncore.ha.statistics import (
    HOUR_MS,
    STATISTICS_UNITS,
    StatisticSpec,
    fetch_quarter_history,
    hourly_values,
    quarter_hours,
)
from suncore.ha.transport import DirectTransport
from suncore.ha.websocket import WebSocketConnection
from tests.ha.fakes import TOKEN, FakeClock, FakeHA

H0 = datetime(2026, 10, 10, 0, 0, tzinfo=UTC)
H0_MS = int(H0.timestamp() * 1000)


def _row(hour: int, **values: Any) -> dict[str, Any]:
    start = H0_MS + hour * HOUR_MS
    return {"start": start, "end": start + HOUR_MS, **values}


# --- parsing ---------------------------------------------------------------------------


def test_mean_rows_become_hourly_values_and_skip_null() -> None:
    rows = [_row(0, mean=100.0), _row(1, mean=None), _row(2, mean=300.0)]
    assert hourly_values(rows, "mean") == {H0_MS: 100.0, H0_MS + 2 * HOUR_MS: 300.0}


def test_rows_off_the_grid_or_not_hourly_are_skipped() -> None:
    odd_start = {"start": H0_MS + 1000, "end": H0_MS + 1000 + HOUR_MS, "mean": 1.0}
    five_minutes = {"start": H0_MS, "end": H0_MS + 300_000, "mean": 2.0}
    no_start = {"mean": 3.0}
    bad_type = {"start": "x", "end": 1, "mean": 4.0}
    assert hourly_values([odd_start, five_minutes, no_start, bad_type], "mean") == {}


def test_energy_uses_change_else_the_difference_of_sum() -> None:
    rows = [
        _row(0, change=500.0, sum=1000.0),
        _row(1, change=None, sum=1800.0),  # 800 from the sums of consecutive hours
        _row(3, change=None, sum=3000.0),  # previous row is not the previous hour: a gap
        _row(4, change=-5.0, sum=3100.0),  # negative: skipped
        _row(5, change=0.0, sum=3100.0),
    ]
    assert hourly_values(rows, "energy") == {
        H0_MS: 500.0,
        H0_MS + HOUR_MS: 800.0,
        H0_MS + 5 * HOUR_MS: 0.0,
    }


def test_booleans_and_non_finite_numbers_are_not_values() -> None:
    rows = [_row(0, mean=True), _row(1, mean=float("nan")), _row(2, mean=float("inf"))]
    assert hourly_values(rows, "mean") == {}


# --- hourly -> quarter hours ---------------------------------------------------------------


def test_step_hold_preserves_energy_and_marks_missing_hours() -> None:
    series = quarter_hours({H0_MS: 1000.0, H0_MS + 2 * HOUR_MS: 400.0}, unit=SeriesUnit.W)
    assert series is not None
    assert series.start == H0
    assert series.step == timedelta(minutes=15)
    assert series.values == (1000.0,) * 4 + (None,) * 4 + (400.0,) * 4
    # energy of the covered hours: 1000 Wh + 400 Wh
    assert sum(v * 0.25 for v in series.values if v is not None) == pytest.approx(1400.0)


def test_inversion_flips_the_sign_without_negative_zero() -> None:
    series = quarter_hours({H0_MS: 250.0, H0_MS + HOUR_MS: 0.0}, unit=SeriesUnit.W, invert=True)
    assert series is not None
    assert series.values == (-250.0,) * 4 + (0.0,) * 4
    assert str(series.values[-1]) == "0.0"


def test_no_values_no_series_and_a_long_range_is_refused() -> None:
    assert quarter_hours({}, unit=SeriesUnit.W) is None
    with pytest.raises(ValueError, match="too long"):
        quarter_hours({H0_MS: 1.0, H0_MS + 400 * 24 * HOUR_MS: 1.0}, unit=SeriesUnit.W)


# --- fetching through the fake connection (sim) ------------------------------------------------


@pytest.fixture
async def sim() -> AsyncIterator[Any]:
    from suncore.sim.fake_ha import FakeHAConnection  # noqa: PLC0415
    from suncore.sim.scenario import load_scenario  # noqa: PLC0415

    scenario = load_scenario("sensor_dropout")
    clock_time = scenario.start

    async def sleep(_delay: float) -> None:
        await asyncio.Event().wait()  # the feed never advances

    conn = FakeHAConnection(scenario, clock=lambda: clock_time, sleep=sleep, autorun=False)
    await conn.connect()
    yield conn
    await conn.close()


async def test_fetch_from_the_sim_recorder_chunks_and_batches(sim: Any) -> None:
    from suncore.sim.fake_ha import GRID_ID, HOUSE_ID, PV_ID  # noqa: PLC0415

    end = sim._scenario.start.replace(minute=0, second=0, microsecond=0)
    specs = [
        StatisticSpec("house", HOUSE_ID),
        StatisticSpec("pv", PV_ID),
        StatisticSpec("grid", GRID_ID, invert=True),
        StatisticSpec("ghost", "sensor.does_not_exist"),
    ]
    result = await fetch_quarter_history(
        sim, specs, end - timedelta(hours=48), end, chunk_days=1, batch_size=2
    )
    assert set(result.series) == {"house", "pv", "grid"}
    assert result.missing == ("sensor.does_not_exist",)
    house = result.series["house"]
    assert house.start == end - timedelta(hours=48)
    assert len(house) == 48 * 4
    assert None not in house.values
    # the sign of `grid` is inverted
    plain = (
        await fetch_quarter_history(
            sim, [StatisticSpec("g", GRID_ID)], end - timedelta(hours=48), end
        )
    ).series["g"]
    assert result.series["grid"].values == tuple(-v for v in plain.values if v is not None)
    # 2 chunks (1 day each) x 2 batches (ids 2 + 2)
    calls = sim.recorder.calls
    assert len({c["start"] for c in calls}) == 2
    assert all(len(c["ids"]) <= 2 for c in calls)
    assert all(c["period"] == "hour" for c in calls)


async def test_fetch_aligns_a_ragged_range_to_whole_hours(sim: Any) -> None:
    from suncore.sim.fake_ha import HOUSE_ID  # noqa: PLC0415

    end = sim._scenario.start.replace(minute=0, second=0, microsecond=0)
    await fetch_quarter_history(
        sim,
        [StatisticSpec("h", HOUSE_ID)],
        end - timedelta(hours=5, minutes=20),
        end + timedelta(minutes=10),
    )
    call = sim.recorder.calls[0]
    assert call["start"] == end - timedelta(hours=6)
    assert call["end"] == end + timedelta(hours=1)


async def test_fetch_validates_its_arguments(sim: Any) -> None:
    with pytest.raises(ValueError, match="before end"):
        await fetch_quarter_history(sim, [], H0, H0)
    with pytest.raises(ValueError, match="positive"):
        await fetch_quarter_history(sim, [], H0, H0 + timedelta(hours=1), chunk_days=0)


async def test_sim_recorder_overrides_errors_and_connection_state(sim: Any) -> None:
    sim.recorder.set_rows("sensor.custom", [_row(0, mean=5.0), _row(1, mean=6.0)])
    got = await sim.statistics_during_period(["sensor.custom"], H0, H0 + timedelta(hours=1))
    assert got == {"sensor.custom": [_row(0, mean=5.0)]}
    sim.recorder.set_rows("sensor.custom", [])
    assert await sim.statistics_during_period(["sensor.custom"], H0) == {}
    assert await sim.statistics_during_period([], H0) == {}
    with pytest.raises(CommandError):
        await sim.statistics_during_period(["sensor.custom"], H0, period="5minute")
    sim.statistics_error = CommandError("unauthorized", "no")
    with pytest.raises(CommandError):
        await sim.statistics_during_period(["sensor.custom"], H0)
    await sim.close()
    sim.statistics_error = None
    with pytest.raises(NotConnectedError):
        await sim.statistics_during_period(["sensor.custom"], H0)


# --- the WebSocket command ---------------------------------------------------------------------


@pytest.fixture
async def ha() -> AsyncIterator[FakeHA]:
    server = FakeHA()
    await server.start()
    yield server
    await server.stop()


@pytest.fixture
async def conn(ha: FakeHA) -> AsyncIterator[WebSocketConnection]:
    clock = FakeClock()
    connection = WebSocketConnection(
        DirectTransport.from_base_url(ha.base_url, TOKEN),
        clock=clock.monotonic,
        sleep=clock.sleep,
        rng=lambda: 0.5,
        heartbeat_interval=777.0,
        heartbeat_timeout=666.0,
        resync_interval=555.0,
        command_timeout=888.0,
        connect_timeout=999.0,
    )
    await asyncio.wait_for(connection.connect(), 5)
    yield connection
    await connection.close()


async def test_command_shape_offsets_units_and_absent_ids(
    conn: WebSocketConnection, ha: FakeHA
) -> None:
    ha.statistics["sensor.house"] = [_row(0, mean=100.0), _row(1, mean=200.0)]
    berlin = timezone(timedelta(hours=2))
    got = await conn.statistics_during_period(
        ["sensor.house", "sensor.missing"],
        datetime(2026, 10, 10, 2, 0, tzinfo=berlin),
        datetime(2026, 10, 10, 4, 0, tzinfo=berlin),
        period="hour",
        types=["mean"],
        units=STATISTICS_UNITS,
    )
    assert got == {"sensor.house": [_row(0, mean=100.0), _row(1, mean=200.0)]}  # missing id absent
    sent = next(m for m in ha.received if m["type"] == "recorder/statistics_during_period")
    assert sent["start_time"] == "2026-10-10T02:00:00+02:00"  # offset kept: never HA-local naive
    assert sent["end_time"] == "2026-10-10T04:00:00+02:00"
    assert sent["period"] == "hour"
    assert sent["types"] == ["mean"]
    assert sent["units"] == {"power": "W", "energy": "Wh"}
    assert sent["statistic_ids"] == ["sensor.house", "sensor.missing"]


async def test_command_optional_fields_validation_and_errors(
    conn: WebSocketConnection, ha: FakeHA
) -> None:
    await conn.statistics_during_period(["sensor.a"], H0)
    sent = next(m for m in ha.received if m["type"] == "recorder/statistics_during_period")
    assert "end_time" not in sent
    assert "types" not in sent
    assert "units" not in sent
    assert await conn.statistics_during_period([], H0) == {}
    with pytest.raises(ValueError, match="period"):
        await conn.statistics_during_period(["sensor.a"], H0, period="minute")
    with pytest.raises(ValueError, match="timezone-aware"):
        await conn.statistics_during_period(["sensor.a"], datetime(2026, 1, 1))  # noqa: DTZ001
    ha.fail_commands["recorder/statistics_during_period"] = ("unknown_command", "Unknown command.")
    with pytest.raises(CommandError):
        await conn.statistics_during_period(["sensor.a"], H0)


async def test_fetch_through_the_websocket_with_energy_and_missing_ids(
    conn: WebSocketConnection, ha: FakeHA
) -> None:
    ha.statistics["sensor.house_power"] = [_row(h, mean=100.0 * (h + 1)) for h in range(3)]
    ha.statistics["sensor.pv_energy"] = [
        _row(0, change=400.0, sum=10_000.0),
        _row(1, change=None, sum=10_900.0),
    ]
    specs = [
        StatisticSpec("house", "sensor.house_power"),
        StatisticSpec("pv", "sensor.pv_energy", kind="energy"),
        StatisticSpec("gone", "sensor.gone"),
    ]
    result = await fetch_quarter_history(conn, specs, H0, H0 + timedelta(hours=3))
    assert result.series["house"].values == (100.0,) * 4 + (200.0,) * 4 + (300.0,) * 4
    assert result.series["pv"].values == (400.0,) * 4 + (900.0,) * 4
    assert result.missing == ("sensor.gone",)
    types = {
        tuple(m["types"]) for m in ha.received if m["type"] == "recorder/statistics_during_period"
    }
    assert types == {("mean",), ("change", "sum")}
