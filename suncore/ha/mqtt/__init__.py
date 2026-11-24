# SPDX-License-Identifier: Apache-2.0
"""The Home Assistant entity surface over MQTT discovery (Phase 6 WP3, ADR-0016).

Read-only entities in this version; the command path follows in PR 6-D. Nothing here writes
to a device or calls the controller or the safety gate.
"""

from suncore.core.haentities import BrokerState, HaEntitiesStatus
from suncore.ha.mqtt.broker import (
    BrokerLookup,
    BrokerSettings,
    SupervisorHttpServices,
    lookup_broker,
    supervisor_services_from_env,
)
from suncore.ha.mqtt.catalogue import ALL_ENTITIES, EVENT_TYPES, EntitySpec, Platform, Scope
from suncore.ha.mqtt.client import LinkState, MqttClient, Will, classify_error
from suncore.ha.mqtt.discovery import (
    DeviceInfo,
    HaDevice,
    Topics,
    Topology,
    build_devices,
    device_payload,
    new_install_id,
    render_state,
)
from suncore.ha.mqtt.service import (
    EntitySource,
    HaEntityService,
    InstallRecord,
    InstallStore,
    SiteEvent,
)
from suncore.ha.mqtt.states import StateItem, StatePublisher

__all__ = [
    "ALL_ENTITIES",
    "EVENT_TYPES",
    "BrokerLookup",
    "BrokerSettings",
    "BrokerState",
    "DeviceInfo",
    "EntitySource",
    "EntitySpec",
    "HaDevice",
    "HaEntitiesStatus",
    "HaEntityService",
    "InstallRecord",
    "InstallStore",
    "LinkState",
    "MqttClient",
    "Platform",
    "Scope",
    "SiteEvent",
    "StateItem",
    "StatePublisher",
    "SupervisorHttpServices",
    "Topics",
    "Topology",
    "Will",
    "build_devices",
    "classify_error",
    "device_payload",
    "lookup_broker",
    "new_install_id",
    "render_state",
    "supervisor_services_from_env",
]
