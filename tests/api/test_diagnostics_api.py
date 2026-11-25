# SPDX-License-Identifier: Apache-2.0
"""``GET /api/v1/diagnostics`` through the ASGI transport (PR 7-I)."""

from __future__ import annotations

from suncore.api import create_app
from suncore.api.diagnostics_models import DiagnosticsOut
from tests.api.conftest import FakeServices, client_for


async def test_the_export_is_served_and_matches_the_schema(services: FakeServices) -> None:
    async with client_for(create_app(services)) as client:
        response = await client.get("/api/v1/diagnostics")
    assert response.status_code == 200
    body = DiagnosticsOut.model_validate(response.json())
    assert body.schema_version == 1
    assert body.versions.app == "9.9.9"
    assert body.versions.ha_core == "2026.11.0"
    assert ("diagnostics", None) in services.calls


async def test_the_path_is_relative_to_the_api_prefix_and_in_the_openapi_document(
    services: FakeServices,
) -> None:
    app = create_app(services)
    assert "/api/v1/diagnostics" in app.openapi()["paths"]
    async with client_for(app) as client:
        assert (await client.post("/api/v1/diagnostics")).status_code == 405
