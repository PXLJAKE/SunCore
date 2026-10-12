# SPDX-License-Identifier: Apache-2.0
"""HTTP and WebSocket API (FastAPI). Depends on ``core``, ``storage`` and the services interface."""

from suncore.api.app import create_app
from suncore.api.services import (
    CandidateInfo,
    HAUnavailableError,
    HealthReport,
    PeerPolicy,
    Services,
)

__all__ = [
    "CandidateInfo",
    "HAUnavailableError",
    "HealthReport",
    "PeerPolicy",
    "Services",
    "create_app",
]
