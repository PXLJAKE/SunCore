# SPDX-License-Identifier: Apache-2.0
"""Composition of the tariff and forecast adapters (Phase 4 WP4/WP5).

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
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime
from typing import Any

from suncore.core.config import ForecastConfig
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


__all__ = ["HaPvDataReader", "build_pv_forecast_service"]
