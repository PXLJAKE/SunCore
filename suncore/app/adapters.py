# SPDX-License-Identifier: Apache-2.0
"""Composition of the tariff and forecast adapters (Phase 4 WP4/WP5).

This module builds the price and forecast sources from the configured templates and hands them
the Home Assistant connection. It is the only module of ``suncore.app`` that may call
``HAConnection.call_action_response`` (``tests/safety/test_boundary.py``, rule 7): the call is a
read-only action with ``return_response`` and is rate limited per ``(domain, service)`` by the
connection (``ActionRateLimiter``). The tariff engine passes ``DEFAULT_MIN_INTERVAL_S`` (30 s,
the limiter's floor) as ``min_interval_s``; how often the service asks is its own refresh
logic. A throttled call means "use the last value"
(:class:`~suncore.tariff.errors.SourceThrottledError`).

* :class:`ConnectionActionCaller` - the ``ActionCaller`` of the tariff engine;
* :func:`cache_reader` - the ``EntityReader`` on top of the entity cache;
* :func:`build_tariff_plan` / :func:`build_tariff_service` - configuration -> service.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, time, tzinfo
from typing import Any

import structlog

from suncore.core.config import RegionConfig, TariffConfig
from suncore.core.site import SiteConfig
from suncore.ha import EntityCache, HAConnection, HAError
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
