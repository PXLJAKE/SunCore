# SPDX-License-Identifier: Apache-2.0
"""``energy/solar_forecast``: the read-only command, its parsing and the fake (Phase 4 WP5).

Shape (``docs/research/ha-websocket-api.md`` "energy/solar_forecast"):
``{"<config_entry_id>": {"wh_hours": {"<ISO 8601 timestamp>": <number>}}}``, ``{}`` when no
forecast integration is linked in the Energy dashboard.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest

from suncore.ha import fetch_solar_forecast
from suncore.ha.connection import CommandError, ProtocolError
from suncore.ha.solar_forecast import parse_solar_forecast
from suncore.ha.transport import DirectTransport
from suncore.ha.websocket import WebSocketConnection
from suncore.sim.fake_ha import FakeHAConnection
from suncore.sim.scenario import load_scenario
from tests.ha.fakes import TOKEN, FakeClock, FakeHA

HOURS = {"2026-06-21T10:00:00+02:00": 1200, "2026-06-21T11:00:00+02:00": 1500.5}


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


async def test_the_command_is_sent_without_fields_and_parsed(
    conn: WebSocketConnection, ha: FakeHA
) -> None:
    ha.solar_forecast = {"01JABC": {"wh_hours": HOURS}, "01JDEF": {"wh_hours": {}}}
    got = await fetch_solar_forecast(conn)
    assert got == {"01JABC": {k: float(v) for k, v in HOURS.items()}, "01JDEF": {}}
    sent = next(m for m in ha.received if m["type"] == "energy/solar_forecast")
    assert set(sent) == {"id", "type"}  # no fields, no admin requirement


async def test_nothing_linked_is_an_empty_result(conn: WebSocketConnection, ha: FakeHA) -> None:
    ha.solar_forecast = {}
    assert await conn.energy_solar_forecast() == {}


async def test_wrong_shapes_and_errors(conn: WebSocketConnection, ha: FakeHA) -> None:
    ha.solar_forecast = ["not", "an", "object"]
    with pytest.raises(ProtocolError):
        await conn.energy_solar_forecast()
    ha.fail_commands["energy/solar_forecast"] = ("unknown_command", "Unknown command.")
    with pytest.raises(CommandError) as err:
        await conn.energy_solar_forecast()
    assert err.value.code == "unknown_command"


def test_parse_drops_malformed_entries_and_values() -> None:
    parsed = parse_solar_forecast(
        {
            "a": {
                "wh_hours": {
                    "2026-06-21T10:00:00+02:00": 5,
                    "bad": "x",
                    "k": True,
                    "n": float("nan"),
                }
            },
            "b": {"nope": {}},
            "c": "junk",
        }
    )
    assert parsed == {"a": {"2026-06-21T10:00:00+02:00": 5.0}}


async def test_fake_serves_the_two_linked_entries_and_respects_the_dashboard() -> None:
    never = asyncio.Event()

    async def sleep(_: float) -> None:
        await never.wait()

    def build(*, configured: bool) -> FakeHAConnection:
        return FakeHAConnection(
            load_scenario("summer_day"),
            clock=lambda: datetime(2026, 6, 21, tzinfo=UTC),
            sleep=sleep,
            start_offset_s=13 * 3600,
            energy_prefs_configured=configured,
        )

    conn = build(configured=True)
    await conn.connect()
    entries = await conn.energy_solar_forecast()
    prefs = await conn.energy_get_prefs()
    assert prefs is not None
    linked = next(s for s in prefs["energy_sources"] if s["type"] == "solar")
    assert set(entries) == set(linked["config_entry_solar_forecast"])
    hourly = entries["sim_entry_forecast_solar"]
    # forecast_solar/energy.py drops only a 0 Wh entry at exactly 00:00:00
    assert not any(v == 0 and k.endswith("T00:00:00+02:00") for k, v in hourly.items())
    assert any(v == 0 for v in hourly.values())  # the other night hours are listed
    half_hourly = entries["sim_entry_solcast_solar"]
    assert len(half_hourly) == 96
    await conn.close()
    off = build(configured=False)
    await off.connect()
    assert await off.energy_solar_forecast() == {}
    await off.close()
