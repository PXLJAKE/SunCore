# SPDX-License-Identifier: Apache-2.0
"""HTTP and WebSocket API (FastAPI). Depends on ``core``, ``storage`` and the services interface."""

from suncore.api.app import create_app
from suncore.api.services import (
    EntityQuery,
    HAUnavailableError,
    HealthReport,
    PeerPolicy,
    Services,
    UnknownRoleError,
    UnknownTemplateError,
)

__all__ = [
    "EntityQuery",
    "HAUnavailableError",
    "HealthReport",
    "PeerPolicy",
    "Services",
    "UnknownRoleError",
    "UnknownTemplateError",
    "create_app",
]
