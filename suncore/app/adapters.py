# SPDX-License-Identifier: Apache-2.0
"""Composition of the tariff and forecast adapters (Phase 4 WP4/WP5/WP6).

This module builds the price and forecast sources from the configured templates and hands them
the Home Assistant connection. It is the only module of ``suncore.app`` that may call
``HAConnection.call_action_response`` (``tests/safety/test_boundary.py``, rule 7): the call is a
read-only action with ``return_response`` and is rate limited per ``(domain, service)`` by the
connection (``ActionRateLimiter``). An adapter passes the polling interval of its template as
``min_interval_s`` and treats ``ActionRateLimitedError`` as "use the last value".

The PV forecast sources read through :class:`HaPvDataReader`: attributes of the entity cache (no
extra Home Assistant call), the Energy dashboard's ``energy/solar_forecast`` through
``suncore.ha.fetch_solar_forecast`` (rule 9 keeps the command inside ``suncore.ha``) and the
read-only action ``solcast_solar.query_forecast_data``.

The load forecast reads through :class:`HaLoadReader`: the ``workday`` binary sensor's state and
the weather entity's ``temperature`` attribute from the entity cache, and the read-only actions
``workday.check_date`` (per date) and ``weather.get_forecasts`` (``type: hourly``).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from datetime import date, datetime, tzinfo
from typing import Any

from suncore.core.config import ForecastConfig
from suncore.forecast.accuracy import LearnedParams
from suncore.forecast.load import (
    parse_check_date,
    parse_hourly_temperatures,
    parse_workday_state,
    temperature_c,
)
from suncore.forecast.load_service import LoadData, LoadForecastService, LoadInputs
from suncore.forecast.pv.factory import build_sources
from suncore.forecast.pv.service import (
    BlendContext,
    ForecastStore,
    PvForecastService,
    SourceSettings,
)
from suncore.ha import EntityCache, HAConnection, fetch_solar_forecast
from suncore.ha.cache import UNAVAILABLE_STATES
from suncore.templates import TemplateCatalog


class HaPvDataReader:
    """The :class:`~suncore.forecast.pv.adapter.PvDataReader` backed by Home Assistant."""

    def __init__(self, connection: HAConnection, cache: EntityCache) -> None:
        self._connection = connection
        self._cache = cache

    def attributes(self, entity_id: str) -> Mapping[str, Any] | None:
        entry = self._cache.get(entity_id)
        if entry is None or entry.state.state in UNAVAILABLE_STATES:
            return None
        return entry.state.attributes

    async def solar_forecast(self) -> Mapping[str, Mapping[str, float]]:
        return await fetch_solar_forecast(self._connection)

    async def action_response(
        self, domain: str, service: str, data: Mapping[str, Any], *, min_interval_s: float
    ) -> Mapping[str, Any]:
        return await self._connection.call_action_response(
            domain, service, data, min_interval_s=min_interval_s
        )


def build_pv_forecast_service(
    config: ForecastConfig | None,
    *,
    catalog: TemplateCatalog,
    connection: HAConnection,
    cache: EntityCache,
    clock: Callable[[], datetime],
    sleep: Callable[[float], Awaitable[None]],
    store: ForecastStore | None,
    context: Callable[[], BlendContext] = BlendContext,
) -> PvForecastService | None:
    """The refresh service of the configured PV sources; ``None`` if none is usable."""
    sources = build_sources(config, catalog, HaPvDataReader(connection, cache))
    if not sources or config is None:
        return None
    settings = {
        item.template_id: SourceSettings(weight_mode=item.weight_mode, manual_weight=item.weight)
        for item in config.sources
    }
    return PvForecastService(
        sources,
        clock=clock,
        sleep=sleep,
        store=store,
        settings=settings,
        min_history_days=config.learning.min_history_days,
        context=context,
    )


class HaLoadReader:
    """The :class:`~suncore.forecast.load_service.LoadHaReader` backed by Home Assistant.

    Shapes verified in ``docs/research/ha-websocket-api.md`` ("Workday", "Weather forecasts").
    ``UNVERIFIED``: the weather entity's ``temperature_unit`` attribute (read for the unit of
    ``temperature`` and of the forecast; a missing unit is taken as °C).
    """

    def __init__(self, connection: HAConnection, cache: EntityCache) -> None:
        self._connection = connection
        self._cache = cache

    def _state(self, entity_id: str) -> tuple[str, Mapping[str, Any]] | None:
        entry = self._cache.get(entity_id)
        if entry is None or entry.state.state in UNAVAILABLE_STATES:
            return None
        return entry.state.state, entry.state.attributes

    def workday_today(self, entity_id: str) -> bool | None:
        current = self._state(entity_id)
        return None if current is None else parse_workday_state(*current)

    async def check_workday(self, entity_id: str, day: date, *, min_interval_s: float) -> bool:
        response = await self._connection.call_action_response(
            "workday",
            "check_date",
            {"check_date": day.isoformat()},
            target={"entity_id": entity_id},
            min_interval_s=min_interval_s,
            rate_key=day.isoformat(),
        )
        workday = parse_check_date(response, entity_id)
        if workday is None:
            msg = f"workday.check_date: no answer for {entity_id}"
            raise ValueError(msg)
        return workday

    def _unit(self, entity_id: str) -> str | None:
        current = self._state(entity_id)
        unit = None if current is None else current[1].get("temperature_unit")
        return unit if isinstance(unit, str) else None

    def outside_temperature(self, entity_id: str) -> float | None:
        current = self._state(entity_id)
        if current is None:
            return None
        return temperature_c(current[1].get("temperature"), self._unit(entity_id))

    async def hourly_temperatures(
        self, entity_id: str, *, min_interval_s: float
    ) -> list[tuple[datetime, float]]:
        response = await self._connection.call_action_response(
            "weather",
            "get_forecasts",
            {"type": "hourly"},
            target={"entity_id": entity_id},
            min_interval_s=min_interval_s,
            rate_key=entity_id,
        )
        return parse_hourly_temperatures(response, entity_id, self._unit(entity_id))


def build_load_forecast_service(
    config: ForecastConfig | None,
    device_ids: tuple[str, ...],
    *,
    data: LoadData | None,
    learned: LearnedParams | None,
    connection: HAConnection,
    cache: EntityCache,
    clock: Callable[[], datetime],
    sleep: Callable[[float], Awaitable[None]],
    tz: Callable[[], tzinfo],
) -> LoadForecastService | None:
    """The load forecast of the site; ``None`` without ``timeseries.db`` (no history)."""
    if data is None:
        return None
    load = None if config is None else config.load
    inputs = LoadInputs(
        device_ids=device_ids,
        workday_entity=None if load is None or load.workday is None else load.workday.entity_id,
        weather_entity=None if load is None or load.weather is None else load.weather.entity_id,
    )
    return LoadForecastService(
        data,
        learned,
        clock=clock,
        sleep=sleep,
        tz=tz,
        inputs=inputs,
        reader=HaLoadReader(connection, cache),
    )


__all__ = [
    "HaLoadReader",
    "HaPvDataReader",
    "build_load_forecast_service",
    "build_pv_forecast_service",
]
