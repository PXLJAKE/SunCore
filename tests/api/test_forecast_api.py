# SPDX-License-Identifier: Apache-2.0
"""``GET /api/v1/forecast/pv`` and ``/forecast/accuracy`` (Phase 4 WP5; read only, no UI)."""

from __future__ import annotations

from suncore.api import create_app
from suncore.core.forecast import AccuracyRecord, HorizonBucket
from suncore.forecast.accuracy import StoredAccuracy, accuracy_view
from suncore.forecast.views import (
    BlendedView,
    BlendWeightsView,
    CalibrationView,
    PvForecastView,
    PvSeriesView,
    PvSourceView,
)
from tests.api.conftest import NOW, FakeServices, client_for


def pv_view() -> PvForecastView:
    series = PvSeriesView(
        start=NOW,
        step_s=1800,
        p10_w=[None, 600.0],
        p50_w=[None, 1000.0],
        p90_w=[None, 1200.0],
    )
    source = PvSourceView(
        source_id="solcast.pv",
        status="ok",
        fetched_at=NOW,
        age_s=12.5,
        stale=False,
        filled_slots=0,
        series=series,
    )
    never = PvSourceView(
        source_id="forecast_solar.pv",
        status="not_linked",
        fetched_at=None,
        age_s=None,
        stale=True,
        filled_slots=0,
        series=None,
    )
    blended = BlendedView(
        method="single_source",
        sources=["solcast.pv"],
        start=NOW,
        step_s=900,
        p10_w=[None, 600.0],
        p50_w=[None, 1000.0],
        p90_w=[None, 1200.0],
        uncalibrated_p50_w=[None, 1000.0],
        calibrated_p50_w=[None, 900.0],
        calibration_active=False,
        weights=[
            BlendWeightsView(
                horizon=HorizonBucket.H0_6,
                basis="equal_no_history",
                manual=False,
                weights={"solcast.pv": 1.0},
            )
        ],
        calibration=[
            CalibrationView(
                source_id="solcast.pv",
                active=False,
                reason="collecting",
                days_with_data=12,
                min_history_days=30,
                cells=40,
                factor_min=0.85,
                factor_max=0.95,
            )
        ],
    )
    return PvForecastView(generated_at=NOW, hours=24, sources=[source, never], blended=blended)


async def test_pv_forecast_shape_and_default_hours(services: FakeServices) -> None:
    services.pv_view = pv_view()
    async with client_for(create_app(services)) as client:
        r = await client.get("/api/v1/forecast/pv")
        r24 = await client.get("/api/v1/forecast/pv", params={"hours": 24})
    assert r.status_code == 200
    assert r24.status_code == 200
    assert services.pv_hours == [48, 24]
    body = r.json()
    assert [s["source_id"] for s in body["sources"]] == ["solcast.pv", "forecast_solar.pv"]
    first = body["sources"][0]
    assert first["series"]["p50_w"] == [None, 1000.0]
    assert first["series"]["step_s"] == 1800
    assert first["stale"] is False
    assert first["age_s"] == 12.5
    never = body["sources"][1]
    assert never["series"] is None
    assert never["fetched_at"] is None
    assert never["stale"] is True
    blend = body["blended"]
    assert blend["method"] == "single_source"
    assert blend["p50_w"] == [None, 1000.0]
    assert blend["uncalibrated_p50_w"] == [None, 1000.0]
    assert blend["calibrated_p50_w"] == [None, 900.0]
    assert blend["calibration_active"] is False
    assert blend["weights"] == [
        {
            "horizon": "0_6h",
            "basis": "equal_no_history",
            "manual": False,
            "weights": {"solcast.pv": 1.0},
        }
    ]
    assert blend["calibration"][0]["reason"] == "collecting"
    assert blend["calibration"][0]["days_with_data"] == 12


async def test_pv_forecast_without_sources(services: FakeServices) -> None:
    async with client_for(create_app(services)) as client:
        body = (await client.get("/api/v1/forecast/pv")).json()
    assert body["sources"] == []
    assert body["blended"] is None


async def test_pv_forecast_hours_are_validated_with_the_standard_error(
    services: FakeServices,
) -> None:
    async with client_for(create_app(services)) as client:
        for hours in (0, 97, "x"):
            r = await client.get("/api/v1/forecast/pv", params={"hours": hours})
            assert r.status_code == 422
    assert services.pv_hours == []


async def test_accuracy_shape(services: FakeServices) -> None:
    record = AccuracyRecord(
        source_id="solcast.pv", horizon=HorizonBucket.H6_24, mae_w=150.5, bias_w=-20.0, n=300
    )
    stored = {"solcast.pv": StoredAccuracy("solcast.pv", NOW, 14, (record,))}
    services.accuracy = accuracy_view(["solcast.pv", "forecast_solar.pv"], stored)
    async with client_for(create_app(services)) as client:
        r = await client.get("/api/v1/forecast/accuracy")
    body = r.json()
    assert r.status_code == 200
    assert body["min_samples"] == 96
    solcast, other = body["sources"]
    assert solcast["records"] == [
        {"source_id": "solcast.pv", "horizon": "6_24h", "mae_w": 150.5, "bias_w": -20.0, "n": 300}
    ]
    assert solcast["window_days"] == 14
    assert solcast["computed_at"] is not None
    assert other["records"] == []
    assert other["computed_at"] is None


async def test_the_openapi_document_describes_both_routes(services: FakeServices) -> None:
    schema = create_app(services).openapi()
    for path in ("/api/v1/forecast/pv", "/api/v1/forecast/accuracy"):
        assert "get" in schema["paths"][path]
    description = schema["paths"]["/api/v1/forecast/pv"]["get"]["description"]
    assert "blended" in description
    assert "calibration" in description
    accuracy = schema["paths"]["/api/v1/forecast/accuracy"]["get"]["description"]
    assert "actual -" in accuracy
    assert "blended" in accuracy
