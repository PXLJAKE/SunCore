# SPDX-License-Identifier: Apache-2.0
"""``GET /api/v1/prices``: the 15-minute planning prices with source and freshness."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from suncore.api import create_app
from suncore.tariff.report import (
    PriceFreshness,
    PriceQuality,
    PriceReport,
    PriceSlot,
    PriceSourceInfo,
)
from tests.api.conftest import FakeServices, client_for

T0 = datetime(2026, 10, 16, 8, 0, tzinfo=UTC)


def report() -> PriceReport:
    return PriceReport(
        start=T0,
        slots=[
            PriceSlot(start=T0, import_price=0.31, export_price=0.08),
            PriceSlot(start=T0 + timedelta(minutes=15), import_price=-0.01, export_price=0.0),
            PriceSlot(start=T0 + timedelta(minutes=30), import_price=None, export_price=None),
        ],
        source=PriceSourceInfo(
            import_source_id="tibber.price",
            import_kind="dynamic",
            source_price_includes="all_in",
            price_includes="all_in",
            formula_applied=False,
            export_kind="fixed",
            negative_price_rule="no_remuneration",
            export_zeroed_slots=1,
            native_step_min=15,
            currency="EUR",
        ),
        freshness=PriceFreshness(
            status="fresh",
            fetched_at=T0,
            age_s=12.5,
            known_until=T0 + timedelta(hours=36),
            next_refresh_at=T0 + timedelta(hours=1),
            last_error=None,
        ),
        quality=PriceQuality(slots=3, known_slots=2, has_tomorrow=True, dropped_points=0),
    )


async def test_prices_are_returned_with_source_and_freshness(services: FakeServices) -> None:
    services.price_report = report()
    async with client_for(create_app(services)) as client:
        r = await client.get("/api/v1/prices")
    assert r.status_code == 200
    body = r.json()
    assert services.price_hours == [48]  # the default
    assert body["unit"] == "currency/kWh"
    assert body["step_min"] == 15
    assert body["slots"][1] == {
        "start": "2026-10-16T08:15:00Z",
        "import_price": -0.01,
        "export_price": 0.0,
    }
    assert body["slots"][2]["import_price"] is None
    assert body["source"]["import_source_id"] == "tibber.price"
    assert body["source"]["negative_price_rule"] == "no_remuneration"
    assert body["freshness"]["status"] == "fresh"
    assert body["freshness"]["last_error"] is None
    assert body["quality"] == {
        "slots": 3,
        "known_slots": 2,
        "has_tomorrow": True,
        "dropped_points": 0,
    }


async def test_hours_parameter_is_passed_and_bounded(services: FakeServices) -> None:
    services.price_report = report()
    async with client_for(create_app(services)) as client:
        ok = await client.get("/api/v1/prices", params={"hours": 24})
        zero = await client.get("/api/v1/prices", params={"hours": 0})
        many = await client.get("/api/v1/prices", params={"hours": 73})
        text = await client.get("/api/v1/prices", params={"hours": "soon"})
    assert ok.status_code == 200
    assert services.price_hours == [24]
    assert zero.status_code == many.status_code == text.status_code == 422
    assert zero.json()["detail"] == "api.error.validation"


async def test_no_tariff_is_a_404_with_a_catalogue_key(services: FakeServices) -> None:
    async with client_for(create_app(services)) as client:
        r = await client.get("/api/v1/prices")
    assert r.status_code == 404
    assert r.json() == {"detail": "api.error.tariff_not_configured"}


async def test_prices_are_in_the_openapi_document(services: FakeServices) -> None:
    async with client_for(create_app(services)) as client:
        schema = (
            (await client.get("/openapi.json")).json() if False else create_app(services).openapi()
        )
    operation = schema["paths"]["/api/v1/prices"]["get"]
    assert operation["operationId"] == "get_prices"
    assert {"200", "404", "422"} <= set(operation["responses"])
    assert "PriceReport" in schema["components"]["schemas"]
