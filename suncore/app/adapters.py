# SPDX-License-Identifier: Apache-2.0
"""Composition of the tariff and forecast adapters (Phase 4 WP4/WP5/WP6).

This module builds the price and forecast sources from the configured templates and hands them
the Home Assistant connection. It is the only module of ``suncore.app`` that may call
``HAConnection.call_action_response`` (``tests/safety/test_boundary.py``, rule 7): the call is a
read-only action with ``return_response`` and is rate limited per ``(domain, service)`` by the
connection (``ActionRateLimiter``). The tariff engine passes ``DEFAULT_MIN_INTERVAL_S`` (30 s,
the limiter's floor) as ``min_interval_s``; how often the service asks is its own refresh
logic. A throttled call means "use the last value"
(:class:`~suncore.tariff.errors.SourceThrottledError`).

The PV forecast sources read through :class:`HaPvDataReader`: attributes of the entity cache (no
extra Home Assistant call), the Energy dashboard's ``energy/solar_forecast`` through
``suncore.ha.fetch_solar_forecast`` (rule 9 keeps the command inside ``suncore.ha``) and the
read-only action ``solcast_solar.query_forecast_data``.

The load forecast reads through :class:`HaLoadReader`: the ``workday`` binary sensor's state and
the weather entity's ``temperature`` attribute from the entity cache, and the read-only actions
``workday.check_date`` (per date) and ``weather.get_forecasts`` (``type: hourly``).

The tariff side provides:

* :class:`ConnectionActionCaller` - the ``ActionCaller`` of the tariff engine;
* :func:`cache_reader` - the ``EntityReader`` on top of the entity cache;
* :func:`build_tariff_plan` / :func:`build_tariff_service` - configuration -> service.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from datetime import date, datetime, time, tzinfo
from typing import Any

import structlog

from suncore.core.config import ForecastConfig, RegionConfig, TariffConfig
from suncore.core.site import SiteConfig
from suncore.forecast.accuracy import LearnedParams
from suncore.forecast.load import (
    WorkdayState,
    parse_check_date,
    parse_hourly_temperatures,
    parse_workday_sensor,
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
from suncore.ha import EntityCache, HAConnection, HAError, fetch_solar_forecast
from suncore.ha.cache import UNAVAILABLE_STATES
from suncore.ha.connection import ActionRateLimitedError
from suncore.tariff import (
    CurrentPriceSource,
    EntityReader,
    EntityReading,
    FixedPriceSource,
    PriceStore,
    SegmentSource,
    ServiceSettings,
    SourceBinding,
    SourceFailedError,
    SourceThrottledError,
    TariffPlan,
    TariffService,
    TemplatePriceSource,
    TouPriceSource,
)
from suncore.templates import RegionTemplate, TariffTemplate, TemplateCatalog
from suncore.templates.models import NegativePriceRule

log = structlog.get_logger(__name__)


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

    Shapes verified in ``docs/research/ha-websocket-api.md`` ("Workday", "Weather forecasts"):
    the weather entity's ``temperature_unit`` attribute is the unit of its ``temperature`` and of
    every ``weather.get_forecasts`` item (both converted to it by Home Assistant).
    """

    def __init__(self, connection: HAConnection, cache: EntityCache) -> None:
        self._connection = connection
        self._cache = cache

    def _state(self, entity_id: str) -> tuple[str, Mapping[str, Any]] | None:
        entry = self._cache.get(entity_id)
        if entry is None or entry.state.state in UNAVAILABLE_STATES:
            return None
        return entry.state.state, entry.state.attributes

    def workday_state(self, entity_id: str) -> WorkdayState | None:
        current = self._state(entity_id)
        return None if current is None else parse_workday_sensor(entity_id, *current)

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


class ConnectionActionCaller:
    """``ActionCaller`` on a Home Assistant connection (read-only actions only).

    A call the rate limiter skips becomes :class:`SourceThrottledError` (not a failure: the
    service keeps the last prices); anything else Home Assistant answers wrong becomes
    :class:`SourceFailedError`.
    """

    def __init__(self, connection: HAConnection) -> None:
        self._connection = connection

    async def __call__(
        self,
        domain: str,
        service: str,
        data: Mapping[str, Any],
        *,
        min_interval_s: float,
        max_wait_s: float,
    ) -> Mapping[str, Any]:
        try:
            return await self._connection.call_action_response(
                domain, service, data, min_interval_s=min_interval_s, max_wait_s=max_wait_s
            )
        except ActionRateLimitedError as exc:
            raise SourceThrottledError(exc.retry_after_s) from exc
        except (HAError, TimeoutError) as exc:
            raise SourceFailedError(f"{type(exc).__name__}: {exc}") from exc


def cache_reader(cache: EntityCache) -> EntityReader:
    """Entity readings from the cache; ``unavailable`` and absent entities read as ``None``.

    ``unknown`` is returned as is: an event entity is ``unknown`` until its first event while
    its attributes may already be restored.
    """

    def read(entity_id: str) -> EntityReading | None:
        entry = cache.get(entity_id)
        if entry is None or entry.state.state == "unavailable":
            return None
        return EntityReading(entry.state.state, entry.state.attributes)

    return read


def negative_price_rule(catalog: TemplateCatalog, region: RegionConfig) -> NegativePriceRule:
    """The region template's rule (``none`` for an unknown or user-defined region)."""
    template = catalog.get(region.region_id)
    return (
        template.negative_price_rule
        if isinstance(template, RegionTemplate)
        else NegativePriceRule.NONE
    )


def _import_source(
    tariff: TariffConfig,
    region: RegionConfig,
    tz: tzinfo,
    catalog: TemplateCatalog,
    *,
    connection: HAConnection,
    cache: EntityCache,
    clock: Callable[[], datetime],
) -> SegmentSource | None:
    imp = tariff.import_
    if imp.kind == "fixed":
        assert imp.fixed_price is not None
        return FixedPriceSource(price=imp.fixed_price)
    if imp.kind == "tou":
        assert imp.tou is not None
        return TouPriceSource(tou=imp.tou, tz=tz)
    reader = cache_reader(cache)
    template = catalog.get(imp.template_id) if imp.template_id is not None else None
    if not isinstance(template, TariffTemplate) or template.price_series is None:
        if imp.template_id is not None:
            log.warning("tariff.template_unusable", template_id=imp.template_id)
            return None
        assert imp.price_entity is not None
        return CurrentPriceSource(
            imp.price_entity.entity_id,
            reader,
            price_includes=imp.price_includes or "all_in",
            clock=clock,
        )
    entities = {role: ref.entity_id for role, ref in imp.role_entities.items()}
    if imp.price_entity is not None:
        entities["price"] = imp.price_entity.entity_id
    binding = SourceBinding(
        tz=tz,
        entities=entities,
        action_params=imp.action_params,
        series_key=imp.series_key,
        price_includes=imp.price_includes,
        currency=region.currency,
    )
    return TemplatePriceSource(
        template,
        binding,
        action=ConnectionActionCaller(connection),
        attributes=reader,
        clock=clock,
    )


def build_tariff_plan(
    tariff: TariffConfig,
    region: RegionConfig,
    site: SiteConfig | None,
    tz: tzinfo,
    catalog: TemplateCatalog,
    *,
    connection: HAConnection,
    cache: EntityCache,
    clock: Callable[[], datetime],
) -> TariffPlan | None:
    """The resolved tariff, ``None`` when its template is missing or not a price curve."""
    source = _import_source(
        tariff, region, tz, catalog, connection=connection, cache=cache, clock=clock
    )
    if source is None:
        return None
    return TariffPlan(
        source=source,
        export_tariff=tariff.export,
        negative_rule=negative_price_rule(catalog, region),
        tz=tz,
        currency=region.currency,
        formula=tariff.import_.formula,
        total_kwp=None if site is None else site.total_kwp,
    )


def build_tariff_service(
    tariff: TariffConfig,
    region: RegionConfig,
    site: SiteConfig | None,
    tz: tzinfo,
    catalog: TemplateCatalog,
    *,
    connection: HAConnection,
    cache: EntityCache,
    clock: Callable[[], datetime],
    store: PriceStore | None,
    refresh_interval_s: float = 3600.0,
    tomorrow_after: time = time(13, 0),
    tomorrow_poll_s: float = 900.0,
) -> TariffService | None:
    """A :class:`TariffService` for the stored tariff, ``None`` if it cannot be built.

    Raises :class:`~suncore.tariff.errors.ExportConfigError` for a tiered export that the
    site's size does not allow (the caller logs it and runs without prices).
    """
    plan = build_tariff_plan(
        tariff, region, site, tz, catalog, connection=connection, cache=cache, clock=clock
    )
    if plan is None:
        return None
    settings = ServiceSettings(
        refresh_interval_s=refresh_interval_s,
        tomorrow_after=tomorrow_after,
        tomorrow_poll_s=tomorrow_poll_s,
    )
    return TariffService(plan, clock=clock, store=store, settings=settings)


__all__ = [
    "ConnectionActionCaller",
    "HaLoadReader",
    "HaPvDataReader",
    "build_load_forecast_service",
    "build_pv_forecast_service",
    "build_tariff_plan",
    "build_tariff_service",
    "cache_reader",
    "negative_price_rule",
]
