# SPDX-License-Identifier: Apache-2.0
"""HTTP and WebSocket API (FastAPI). Depends on ``core``, ``storage`` and the services interface."""

from suncore.api.app import create_app
from suncore.api.services import (
    EnergyPrefsFailedError,
    EntityQuery,
    HAUnavailableError,
    HealthReport,
    PeerPolicy,
    Services,
    TariffNotConfiguredError,
    UnknownRoleError,
    UnknownTemplateError,
)

__all__ = [
    "EnergyPrefsFailedError",
    "EntityQuery",
    "HAUnavailableError",
    "HealthReport",
    "PeerPolicy",
    "Services",
    "TariffNotConfiguredError",
    "UnknownRoleError",
    "UnknownTemplateError",
    "create_app",
]
