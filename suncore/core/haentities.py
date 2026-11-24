# SPDX-License-Identifier: Apache-2.0
"""Status of the Home Assistant entity surface (MQTT discovery, Phase 6 WP3, ADR-0016).

Pure data. The MQTT package produces it, the API and the system check read it; neither knows
the other.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class BrokerState(StrEnum):
    """Status of the HA entity surface (API ``GET /api/v1/ha-entities/status``)."""

    DISABLED = "disabled"
    NO_BROKER = "no_broker"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class HaEntitiesStatus:
    state: BrokerState
    source: str | None
    """``supervisor`` or ``manual``; ``None`` while no broker is looked up."""
    entity_count: int
    reason_key: str | None
    """Why the surface is not connected (``mqtt.reason.*``); ``None`` while it works."""
