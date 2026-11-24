# SPDX-License-Identifier: Apache-2.0
"""Device-based MQTT discovery payloads and the topic layout (ADR-0016 decision 3).

Pure functions: a :class:`Topology` (what exists in SunCore) plus the installation id give the
HA devices, their entities and the JSON payloads. Keys are the abbreviations of
``homeassistant/components/mqtt/abbreviations.py``
(verified against ``home-assistant/core`` ``dev``,
read 2026-11-24; see ``docs/research/mqtt-discovery.md``).

Not used on purpose: ``translation_key`` (the MQTT entity schemas have none), ``cu``
(configuration_url: the ingress URL is not known to the app without Supervisor API access).
"""

from __future__ import annotations

import json
import secrets
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Final

from suncore.core.devices import DeviceKind
from suncore.ha.mqtt.catalogue import (
    BATTERY_ENTITIES,
    EVENT_TYPES,
    SITE_ENTITIES,
    EntitySpec,
    Platform,
    specs_for_device,
)

BIRTH_TOPIC: Final = "homeassistant/status"
"""HA's birth and last-will topic (default; configurable in HA, UNVERIFIED for custom setups)."""
BIRTH_PAYLOAD: Final = "online"
PAYLOAD_ONLINE: Final = "online"
PAYLOAD_OFFLINE: Final = "offline"
ORIGIN_NAME: Final = "SunCore"
SUPPORT_URL: Final = "https://github.com/pxljake/suncore"
TOPIC_ROOT: Final = "suncore"

Renderer = Callable[[str, Mapping[str, Any] | None], str]
"""``(key, args) -> text``: the catalogue's render function (language already chosen)."""


def new_install_id() -> str:
    """8 random hex characters; not derived from anything personal (ADR-0016)."""
    return secrets.token_hex(4)


@dataclass(frozen=True, slots=True)
class DeviceInfo:
    """A configured SunCore device as the HA surface needs to know it."""

    id: str
    name: str
    kind: DeviceKind


@dataclass(frozen=True, slots=True)
class Topology:
    """What the HA surface announces; everything in it comes from the configuration."""

    devices: tuple[DeviceInfo, ...] = ()
    has_battery: bool = False
    supported: frozenset[str] = frozenset()
    """Catalogue keys the entity source can fill with values (nothing else is announced)."""
    currency: str | None = None
    version: str = "0"
    commands: bool = False
    """A command port is wired: the command entities of ``supported`` are announced."""
    allow_mode_raise: bool = False
    """``mqtt.allow_mode_raise``: only then is the "clear emergency stop" button announced."""


@dataclass(frozen=True, slots=True)
class Topics:
    iid: str
    prefix: str = "homeassistant"

    @property
    def base(self) -> str:
        return f"{TOPIC_ROOT}/{self.iid}"

    @property
    def status(self) -> str:
        return f"{self.base}/status"

    @property
    def event(self) -> str:
        return f"{self.base}/event"

    def state(self, object_id: str) -> str:
        return f"{self.base}/{object_id}/state"

    def command(self, object_id: str) -> str:
        return f"{self.base}/{object_id}/set"

    @property
    def command_filter(self) -> str:
        """Subscription filter of every command topic (``+`` = the object id)."""
        return f"{self.base}/+/set"

    def object_id_of_command(self, topic: str) -> str | None:
        """The object id a command topic belongs to, ``None`` for any other topic."""
        head = f"{self.base}/"
        if not (topic.startswith(head) and topic.endswith("/set")):
            return None
        object_id = topic[len(head) : -len("/set")]
        return object_id if object_id and "/" not in object_id else None

    def config(self, device_object_id: str) -> str:
        return f"{self.prefix}/device/{device_object_id}/config"


@dataclass(frozen=True, slots=True)
class Entity:
    spec: EntitySpec
    object_id: str
    device_object_id: str


@dataclass(frozen=True, slots=True)
class HaDevice:
    object_id: str
    """Discovery object id and the device identifier: ``suncore_<iid>[_<device>|_battery]``."""
    name: str
    model: str
    entities: tuple[Entity, ...] = field(default=())
    via: str | None = None


def _wanted(spec: EntitySpec, topology: Topology) -> bool:
    if spec.command and not topology.commands:
        return False  # no command port wired: command entities are not announced
    if spec.key not in topology.supported:
        return False
    if spec.key == "emergency_stop_clear" and not topology.allow_mode_raise:
        return False  # clearing the stop from HA is part of the opt-in (ADR-0016 decision 5)
    return not (spec.device_class == "monetary" and not topology.currency)


def build_devices(iid: str, topology: Topology, render: Renderer) -> list[HaDevice]:
    """The HA devices with their entities, in a stable order; empty devices are left out."""
    site_id = f"suncore_{iid}"
    taken: set[str] = set()

    def pick(specs: list[EntitySpec], device_id: str | None, owner: str) -> tuple[Entity, ...]:
        found: list[Entity] = []
        for spec in specs:
            if not _wanted(spec, topology):
                continue
            object_id = spec.object_id(device_id)
            if object_id in taken:
                continue  # a device id that collides with a site object id: first one wins
            taken.add(object_id)
            found.append(Entity(spec, object_id, owner))
        return tuple(found)

    devices = [
        HaDevice(
            site_id,
            render("mqtt.device.site", None),
            "Site",
            pick(list(SITE_ENTITIES), None, site_id),
        )
    ]
    for info in topology.devices:
        owner = f"{site_id}_{info.id}"
        devices.append(
            HaDevice(
                owner,
                info.name,
                info.kind.value,
                pick(specs_for_device(info.kind), info.id, owner),
                via=site_id,
            )
        )
    if topology.has_battery:
        owner = f"{site_id}_battery"
        devices.append(
            HaDevice(
                owner,
                render("mqtt.device.battery", {"name": render("mqtt.device.site", None)}),
                "Battery",
                pick(list(BATTERY_ENTITIES), None, owner),
                via=site_id,
            )
        )
    return [d for d in devices if d.entities]


def render_state(spec: EntitySpec, value: object, render: Renderer) -> str:
    """The payload of a state: enum slugs become the rendered label, numbers plain text."""
    if isinstance(value, bool):
        return "ON" if value else "OFF"
    if spec.enum_group is not None:
        return render(f"mqtt.state.{spec.enum_group}.{value}", None)
    if isinstance(value, int | float):
        if spec.precision is not None:
            return f"{value:.{spec.precision}f}"
        return str(round(value, 3))
    return str(value)


def _component(  # noqa: PLR0912 - one branch per platform key
    entity: Entity,
    topics: Topics,
    topology: Topology,
    iid: str,
    render: Renderer,
) -> dict[str, Any]:
    spec = entity.spec
    name = render(spec.name_key, None)
    payload: dict[str, Any] = {
        "p": spec.platform.value,
        "name": name,
        "uniq_id": f"suncore_{iid}_{entity.object_id}",
        "def_ent_id": f"{spec.platform.value}.suncore_{entity.object_id}",
    }
    if spec.platform is Platform.EVENT:
        payload["stat_t"] = topics.event
        payload["evt_typ"] = list(EVENT_TYPES)
        return payload
    if spec.command:
        payload["cmd_t"] = topics.command(entity.object_id)
    if spec.platform is not Platform.BUTTON:  # a button has no state
        payload["stat_t"] = topics.state(entity.object_id)
    # The payloads are announced, not left to HA's defaults (UNVERIFIED defaults, N1).
    if spec.platform is Platform.BUTTON and spec.command:
        payload["pl_prs"] = "PRESS"
    if spec.platform is Platform.SWITCH:
        payload.update({"pl_on": "ON", "pl_off": "OFF", "stat_on": "ON", "stat_off": "OFF"})
    if spec.platform is Platform.NUMBER:
        if spec.unit is not None:
            payload["unit_of_meas"] = spec.unit
        if spec.min_value is not None:
            payload["min"] = spec.min_value
        if spec.max_value is not None:
            payload["max"] = spec.max_value
        if spec.step is not None:
            payload["step"] = spec.step
        payload["mode"] = "box"
    if spec.platform is Platform.SENSOR:
        if spec.device_class is not None:
            payload["dev_cla"] = spec.device_class
        if spec.enum_group is not None:
            payload["ops"] = [
                render(f"mqtt.state.{spec.enum_group}.{v}", None) for v in spec.enum_values
            ]
        elif spec.device_class == "monetary":
            payload["unit_of_meas"] = topology.currency
        elif spec.unit is not None:
            payload["unit_of_meas"] = spec.unit
        if spec.state_class is not None and spec.enum_group is None:
            payload["stat_cla"] = spec.state_class
        if spec.precision is not None and spec.enum_group is None:
            payload["sug_dsp_prc"] = spec.precision
    if spec.entity_category is not None:
        payload["ent_cat"] = spec.entity_category
    return payload


def device_payload(
    iid: str,
    device: HaDevice,
    topics: Topics,
    topology: Topology,
    render: Renderer,
) -> dict[str, Any]:
    """The JSON document for ``<prefix>/device/<object_id>/config``."""
    dev: dict[str, Any] = {
        "ids": [device.object_id],
        "name": device.name,
        "mf": ORIGIN_NAME,
        "mdl": device.model,
        "sw": topology.version,
    }
    if device.via is not None:
        dev["via_device"] = device.via
    return {
        "dev": dev,
        "o": {"name": ORIGIN_NAME, "sw": topology.version, "url": SUPPORT_URL},
        "avty_t": topics.status,
        "pl_avail": PAYLOAD_ONLINE,
        "pl_not_avail": PAYLOAD_OFFLINE,
        "cmps": {
            e.object_id: _component(e, topics, topology, iid, render) for e in device.entities
        },
    }


def encode(payload: Mapping[str, Any]) -> str:
    """Compact, key-sorted JSON (stable for golden tests)."""
    return json.dumps(payload, separators=(",", ":"), sort_keys=True, ensure_ascii=False)
