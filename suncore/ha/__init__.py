# SPDX-License-Identifier: Apache-2.0
"""Home Assistant connector: ``HAConnection`` protocol, transports, WebSocket client, cache.

Nothing outside this package knows which transport runs (ADR-0010).
"""

from suncore.ha.cache import CachedEntity, EntityCache
from suncore.ha.candidates import EntityCandidate, entity_candidates
from suncore.ha.connection import (
    AuthError,
    CommandError,
    CommandTimeoutError,
    ConnectionLostError,
    ConnectionState,
    EntityState,
    EntityUpdate,
    HAConnection,
    HAError,
    HeartbeatTimeoutError,
    NotConnectedError,
    ProtocolError,
    RateLimiter,
    ServiceCaller,
    ServiceCallerProvider,
    ServiceResult,
)
from suncore.ha.transport import (
    DirectTransport,
    SupervisorTransport,
    Transport,
    TransportConfigError,
    transport_from_env,
)
from suncore.ha.websocket import Backoff, WebSocketConnection

__all__ = [
    "AuthError",
    "Backoff",
    "CachedEntity",
    "CommandError",
    "CommandTimeoutError",
    "ConnectionLostError",
    "ConnectionState",
    "DirectTransport",
    "EntityCache",
    "EntityCandidate",
    "EntityState",
    "EntityUpdate",
    "HAConnection",
    "HAError",
    "HeartbeatTimeoutError",
    "NotConnectedError",
    "ProtocolError",
    "RateLimiter",
    "ServiceCaller",
    "ServiceCallerProvider",
    "ServiceResult",
    "SupervisorTransport",
    "Transport",
    "TransportConfigError",
    "WebSocketConnection",
    "entity_candidates",
    "transport_from_env",
]
