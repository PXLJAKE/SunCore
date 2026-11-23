# SPDX-License-Identifier: Apache-2.0
"""Composition of the tariff service: the action caller, the cache reader, the builders."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from suncore.app.adapters import (
    ConnectionActionCaller,
    build_tariff_plan,
    build_tariff_service,
    cache_reader,
    negative_price_rule,
)
from suncore.core.config import ExportTariff, ExportTier, ImportTariff, RegionConfig, TariffConfig
from suncore.core.site import EntityRef, GridConfig, PvArray, SiteConfig
from suncore.core.tariff import PriceFormula, TouPeriod, TouTariff, TouWindow
from suncore.ha import EntityCache, HAError
from suncore.ha.connection import ActionRateLimitedError, EntityState, EntityUpdate
from suncore.tariff import (
    CurrentPriceSource,
    ExportConfigError,
    FixedPriceSource,
    SourceFailedError,
    SourceThrottledError,
    TemplatePriceSource,
    TouPriceSource,
)
from suncore.templates.models import NegativePriceRule
from tests.tariff.helpers import CATALOG, NOW

BERLIN = ZoneInfo("Europe/Berlin")
DE = RegionConfig(region_id="de", currency="EUR", timezone="Europe/Berlin")
SITE = SiteConfig(
    grid=GridConfig(power=EntityRef(entity_id="sensor.grid")),
    pv=[PvArray(name="PV", power=EntityRef(entity_id="sensor.pv"), kwp=17.0)],
)


class Connection:
    """Just enough of ``HAConnection`` for the action caller."""

    def __init__(self) -> None:
        self.error: Exception | None = None
        self.calls: list[tuple[str, str, Mapping[str, Any] | None, float | None, float]] = []

    async def call_action_response(
        self,
        domain: str,
        service: str,
        data: Mapping[str, Any] | None = None,
        *,
        target: Mapping[str, Any] | None = None,  # noqa: ARG002
        min_interval_s: float | None = None,
        max_wait_s: float = 0.0,
    ) -> dict[str, Any]:
        self.calls.append((domain, service, data, min_interval_s, max_wait_s))
        if self.error is not None:
            raise self.error
        return {"ok": True}


async def test_the_action_caller_passes_the_request_and_the_interval_through() -> None:
    conn = Connection()
    caller = ConnectionActionCaller(conn)  # type: ignore[arg-type]
    out = await caller("tibber", "get_prices", {"start": "x"}, min_interval_s=45.0, max_wait_s=31.0)
    assert out == {"ok": True}
    assert conn.calls == [("tibber", "get_prices", {"start": "x"}, 45.0, 31.0)]


async def test_a_rate_limited_call_becomes_a_throttle_not_a_failure() -> None:
    conn = Connection()
    conn.error = ActionRateLimitedError("tibber.get_prices", 12.5)
    with pytest.raises(SourceThrottledError) as err:
        await ConnectionActionCaller(conn)(
            "tibber", "get_prices", {}, min_interval_s=30.0, max_wait_s=0.0
        )  # type: ignore[arg-type]
    assert err.value.retry_after_s == 12.5


@pytest.mark.parametrize("error", [HAError("down"), TimeoutError("slow")])
async def test_home_assistant_errors_become_source_failures(error: Exception) -> None:
    conn = Connection()
    conn.error = error
    with pytest.raises(SourceFailedError):
        await ConnectionActionCaller(conn)(
            "tibber", "get_prices", {}, min_interval_s=30.0, max_wait_s=0.0
        )  # type: ignore[arg-type]


def test_cache_reader() -> None:
    cache = EntityCache()
    cache.ingest(
        EntityUpdate(
            changed={
                "sensor.ok": EntityState("sensor.ok", "0.3", {"prices": []}),
                "sensor.gone": EntityState("sensor.gone", "unavailable", {"prices": []}),
                "event.new": EntityState("event.new", "unknown", {"event_types": ["x"]}),
            },
            full=True,
        )
    )
    read = cache_reader(cache)
    ok = read("sensor.ok")
    assert ok is not None
    assert (ok.state, dict(ok.attributes)) == ("0.3", {"prices": []})
    assert read("sensor.gone") is None
    assert read("sensor.absent") is None
    unknown = read("event.new")
    assert unknown is not None  # an event entity is `unknown` until its first event
    assert unknown.state == "unknown"


def test_the_negative_price_rule_comes_from_the_region_template() -> None:
    assert negative_price_rule(CATALOG, DE) is NegativePriceRule.NO_REMUNERATION
    generic = RegionConfig(region_id="generic", currency="EUR", timezone="UTC")
    assert negative_price_rule(CATALOG, generic) is NegativePriceRule.NONE
    other = RegionConfig(region_id="xx", currency="EUR", timezone="UTC")
    assert negative_price_rule(CATALOG, other) is NegativePriceRule.NONE


def build(tariff: TariffConfig, site: SiteConfig | None = SITE) -> Any:
    return build_tariff_plan(
        tariff,
        DE,
        site,
        BERLIN,
        CATALOG,
        connection=Connection(),  # type: ignore[arg-type]
        cache=EntityCache(),
        clock=lambda: NOW,
    )


EXPORT = ExportTariff(kind="fixed", fixed_price=0.08)


def test_a_fixed_tariff_builds_a_fixed_source() -> None:
    plan = build(TariffConfig(import_=ImportTariff(kind="fixed", fixed_price=0.3), export=EXPORT))
    assert isinstance(plan.source, FixedPriceSource)
    assert plan.negative_rule is NegativePriceRule.NO_REMUNERATION
    assert plan.total_kwp == 17.0
    assert plan.currency == "EUR"


def test_a_tou_tariff_builds_a_tou_source() -> None:
    tou = TouTariff(
        base_price=0.35,
        periods=(TouPeriod(window=TouWindow(start="22:00", end="06:00"), price=0.25),),
    )
    plan = build(TariffConfig(import_=ImportTariff(kind="tou", tou=tou), export=EXPORT))
    assert isinstance(plan.source, TouPriceSource)


def test_a_dynamic_tariff_builds_the_template_source_with_the_users_settings() -> None:
    tariff = TariffConfig(
        import_=ImportTariff(
            kind="dynamic",
            template_id="octopus_energy.rates",
            price_entity=EntityRef(entity_id="sensor.rate"),
            role_entities={
                "current_day_rates": EntityRef(entity_id="event.today"),
                "next_day_rates": EntityRef(entity_id="event.tomorrow"),
            },
            price_includes="spot_only",
            formula=PriceFormula(grid_fee=0.1),
        ),
        export=EXPORT,
    )
    plan = build(tariff)
    assert isinstance(plan.source, TemplatePriceSource)
    assert plan.source.watched_entities == ("event.today", "event.tomorrow", "sensor.rate")
    assert plan.source.price_includes == "spot_only"  # the user's statement beats the template
    assert plan.formula is not None


def test_a_price_entity_without_a_template_uses_the_current_slot_only() -> None:
    tariff = TariffConfig(
        import_=ImportTariff(kind="dynamic", price_entity=EntityRef(entity_id="sensor.p")),
        export=EXPORT,
    )
    assert isinstance(build(tariff).source, CurrentPriceSource)


def test_an_unknown_or_unsuitable_template_builds_nothing() -> None:
    unknown = TariffConfig(
        import_=ImportTariff(kind="dynamic", template_id="no.such"), export=EXPORT
    )
    assert build(unknown) is None
    not_a_tariff = TariffConfig(
        import_=ImportTariff(kind="dynamic", template_id="lg.thinq_climate"), export=EXPORT
    )
    assert build(not_a_tariff) is None


def test_the_service_gets_the_configured_timing_and_the_store() -> None:
    class Store:
        async def store_prices(self, prices: object) -> int:  # noqa: ARG002
            return 0

    store = Store()
    service = build_tariff_service(
        TariffConfig(import_=ImportTariff(kind="fixed", fixed_price=0.3), export=EXPORT),
        DE,
        SITE,
        BERLIN,
        CATALOG,
        connection=Connection(),  # type: ignore[arg-type]
        cache=EntityCache(),
        clock=lambda: NOW,
        store=store,
        refresh_interval_s=1800.0,
    )
    assert service is not None
    assert service._settings.refresh_interval_s == 1800.0
    assert service._store is store


def test_a_tiered_export_needs_the_site_size() -> None:
    tiered = ExportTariff(
        kind="tiered", tiers=(ExportTier(up_to_kwp=10, price=0.08), ExportTier(price=0.07))
    )
    tariff = TariffConfig(import_=ImportTariff(kind="fixed", fixed_price=0.3), export=tiered)
    kwargs: dict[str, Any] = {
        "connection": Connection(),
        "cache": EntityCache(),
        "clock": lambda: NOW,
        "store": None,
    }
    assert build_tariff_service(tariff, DE, SITE, BERLIN, CATALOG, **kwargs) is not None
    with pytest.raises(ExportConfigError):
        build_tariff_service(tariff, DE, None, BERLIN, CATALOG, **kwargs)
