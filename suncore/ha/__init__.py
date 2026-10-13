# SPDX-License-Identifier: Apache-2.0
"""Home Assistant connector: ``HAConnection`` protocol, transports, WebSocket client, cache.

Nothing outside this package knows which transport runs (ADR-0010).
"""

from suncore.ha.cache import CachedEntity, EntityCache
from suncore.ha.candidates import EntityCandidate, entity_candidates
from suncore.ha.connection import (
    REGISTRY_EVENT_TYPES,
    ActionNotAllowedError,
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
    RegistryListener,
    ServiceCaller,
    ServiceCallerProvider,
    ServiceResult,
)
from suncore.ha.registry import (
    EntityCatalogue,
    RegistryDevice,
    RegistryEntity,
    RegistrySnapshot,
    build_meta,
    parse_area_registry,
    parse_device_registry,
    parse_entity_registry,
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
    "REGISTRY_EVENT_TYPES",
    "ActionNotAllowedError",
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
    "EntityCatalogue",
    "EntityState",
    "EntityUpdate",
    "HAConnection",
    "HAError",
    "HeartbeatTimeoutError",
    "NotConnectedError",
    "ProtocolError",
    "RateLimiter",
    "RegistryDevice",
    "RegistryEntity",
    "RegistryListener",
    "RegistrySnapshot",
    "ServiceCaller",
    "ServiceCallerProvider",
    "ServiceResult",
    "SupervisorTransport",
    "Transport",
    "TransportConfigError",
    "WebSocketConnection",
    "build_meta",
    "entity_candidates",
    "parse_area_registry",
    "parse_device_registry",
    "parse_entity_registry",
    "transport_from_env",
]
