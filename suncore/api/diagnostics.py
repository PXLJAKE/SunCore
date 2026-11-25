# SPDX-License-Identifier: Apache-2.0
"""``GET /api/v1/diagnostics`` (the models are in :mod:`suncore.api.diagnostics_models`)."""

from __future__ import annotations

from fastapi import FastAPI

from suncore.api.diagnostics_models import DiagnosticsOut
from suncore.api.services import Services

API_PREFIX = "/api/v1"


def register_diagnostics(app: FastAPI, services: Services) -> None:
    @app.get(f"{API_PREFIX}/diagnostics", response_model=DiagnosticsOut)
    async def diagnostics() -> DiagnosticsOut:
        """Anonymised diagnostics for bug reports (BRIEF section 5).

        Built from an allowlist: entity ids, device names and HA area names become pseudonyms
        (a new salt per call, the same pseudonym for the same value inside one document),
        secrets are masked, and coordinates, tokens, passwords and MQTT credentials are never
        included. Contains the last 200 decisions and the last 500 warning lines.
        """
        return await services.diagnostics()
