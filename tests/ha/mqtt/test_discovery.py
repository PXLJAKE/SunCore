# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from suncore.core.devices import DeviceKind
from suncore.ha.mqtt import DeviceInfo, Topics, build_devices, device_payload, render_state
from suncore.ha.mqtt.catalogue import ALL_ENTITIES, spec_by_key
from suncore.ha.mqtt.discovery import encode
from suncore.i18n import load_catalogue

from .support import topology

GOLDEN = Path(__file__).resolve().parents[2] / "fixtures" / "mqtt_discovery"
IID = "a1b2c3d4"


def _payloads(language: str, **kw: Any) -> dict[str, dict[str, Any]]:
    render = load_catalogue(language).render
    topo = topology(**kw)
    topics = Topics(IID)
    return {
        d.object_id: device_payload(IID, d, topics, topo, render)
        for d in build_devices(IID, topo, render)
    }


@pytest.mark.parametrize("language", ["en", "de"])
def test_payloads_match_the_golden_files(language: str) -> None:
    payloads = _payloads(language)
    GOLDEN.mkdir(parents=True, exist_ok=True)
    path = GOLDEN / f"{language}.json"
    text = json.dumps(payloads, indent=1, sort_keys=True, ensure_ascii=False) + "\n"
    if os.environ.get("UPDATE_GOLDEN") == "1":
        path.write_text(text, encoding="utf-8")
    assert path.read_text(encoding="utf-8") == text


def test_devices_are_site_devices_and_battery_with_via_device() -> None:
    payloads = _payloads("en")
    assert set(payloads) == {
        f"suncore_{IID}",
        f"suncore_{IID}_heater",
        f"suncore_{IID}_car",
        f"suncore_{IID}_washer",
        f"suncore_{IID}_ac",
        f"suncore_{IID}_battery",
    }
    site = payloads[f"suncore_{IID}"]
    assert "via_device" not in site["dev"]
    for object_id, payload in payloads.items():
        assert payload["dev"]["ids"] == [object_id]
        if object_id != f"suncore_{IID}":
            assert payload["dev"]["via_device"] == f"suncore_{IID}"


def test_root_keys_origin_and_availability() -> None:
    payload = _payloads("en")[f"suncore_{IID}"]
    assert payload["o"] == {
        "name": "SunCore",
        "sw": "9.9.9",
        "url": "https://github.com/pxljake/suncore",
    }
    assert payload["avty_t"] == f"suncore/{IID}/status"
    assert (payload["pl_avail"], payload["pl_not_avail"]) == ("online", "offline")


def test_every_component_has_platform_unique_id_and_default_entity_id() -> None:
    seen: set[str] = set()
    for payload in _payloads("en").values():
        for key, comp in payload["cmps"].items():
            assert key not in seen
            seen.add(key)
            assert comp["uniq_id"] == f"suncore_{IID}_{key}"
            assert comp["def_ent_id"] == f"{comp['p']}.suncore_{key}"
            assert comp["name"]
            assert comp["stat_t"].startswith(f"suncore/{IID}/")


def test_read_only_announcement_has_no_command_topics() -> None:
    for payload in _payloads("en").values():
        for comp in payload["cmps"].values():
            assert "cmd_t" not in comp
            assert comp["p"] in {"sensor", "binary_sensor", "event"}


def test_command_entities_are_never_announced_before_6d() -> None:
    everything = frozenset(s.key for s in ALL_ENTITIES)
    for payload in _payloads("en", supported=everything).values():
        for comp in payload["cmps"].values():
            assert "cmd_t" not in comp


def test_unsupported_entities_are_not_announced() -> None:
    payloads = _payloads("en", supported=frozenset({"status", "device_power"}))
    assert set(payloads) == {
        f"suncore_{IID}",
        f"suncore_{IID}_heater",
        f"suncore_{IID}_car",
        f"suncore_{IID}_washer",
        f"suncore_{IID}_ac",
    }
    assert set(payloads[f"suncore_{IID}"]["cmps"]) == {"status"}
    assert set(payloads[f"suncore_{IID}_heater"]["cmps"]) == {"heater_power"}


def test_enum_sensor_options_are_the_rendered_labels() -> None:
    mode = _payloads("de")[f"suncore_{IID}"]["cmps"]["mode"]
    assert mode["dev_cla"] == "enum"
    assert mode["ops"] == ["Schatten", "Assistiert", "Automatisch"]
    assert "unit_of_meas" not in mode
    assert "stat_cla" not in mode  # HA rejects options with a state class or unit


def test_power_sensor_keys() -> None:
    comp = _payloads("en")[f"suncore_{IID}_heater"]["cmps"]["heater_power"]
    assert comp["dev_cla"] == "power"
    assert comp["unit_of_meas"] == "W"
    assert comp["stat_cla"] == "measurement"
    assert comp["sug_dsp_prc"] == 0


def test_event_entity() -> None:
    comp = _payloads("en")[f"suncore_{IID}"]["cmps"]["events"]
    assert comp["p"] == "event"
    assert comp["stat_t"] == f"suncore/{IID}/event"
    assert "device_switched" in comp["evt_typ"]
    assert "cmd_t" not in comp


def test_monetary_needs_a_currency() -> None:
    with_currency = _payloads("en", supported=frozenset({"savings_today"}))
    comp = with_currency[f"suncore_{IID}"]["cmps"]["savings_today"]
    assert (comp["dev_cla"], comp["unit_of_meas"]) == ("monetary", "EUR")
    assert _payloads("en", supported=frozenset({"savings_today"}), currency=None) == {}


def test_a_device_id_that_collides_with_a_site_object_is_skipped() -> None:
    topo = topology(
        devices=(DeviceInfo("battery", "Odd", DeviceKind.SWITCH),),
        supported=frozenset({"battery_action", "device_power", "device_mode"}),
        has_battery=True,
    )
    render = load_catalogue("en").render
    devices = build_devices(IID, topo, render)
    ids = [e.object_id for d in devices for e in d.entities]
    assert len(ids) == len(set(ids))


def test_render_state() -> None:
    render = load_catalogue("de").render
    mode = spec_by_key("mode")
    power = spec_by_key("device_power")
    healthy = spec_by_key("healthy")
    assert mode is not None
    assert power is not None
    assert healthy is not None
    assert render_state(mode, "automatic", render) == "Automatisch"
    assert render_state(power, 1234.4, render) == "1234"
    assert render_state(healthy, True, render) == "ON"
    assert render_state(healthy, False, render) == "OFF"


def test_encode_is_compact_and_stable() -> None:
    assert encode({"b": 1, "a": "ä"}) == '{"a":"ä","b":1}'
