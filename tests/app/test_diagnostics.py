# SPDX-License-Identifier: Apache-2.0
"""The anonymised diagnostics export (PR 7-I): allowlist, pseudonyms, secrets, completeness."""

from __future__ import annotations

import io
import json
import logging
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import structlog
from pydantic import SecretStr

from suncore.api import create_app
from suncore.app.diagnostics import (
    DECISIONS_LIMIT,
    LOG_LIMIT,
    DiagnosticsInputs,
    EntityFacts,
    ScopeFacts,
    build_diagnostics,
    redact_config,
)
from suncore.app.logbuffer import LOG_RING, LOG_RING_SIZE, LogRing
from suncore.app.logconfig import configure_logging
from suncore.app.pseudonyms import Pseudonymizer
from suncore.app.runtime import Runtime
from suncore.core.config import ImportTariff, MqttConfig, TariffConfig
from suncore.core.decisions import (
    Decision,
    DecisionKind,
    Intent,
    ModeAction,
    OperatingMode,
    Origin,
    Reason,
    SetpointAction,
    SwitchAction,
)
from suncore.ha.redact import DEFAULT_REGISTRY, register_secret
from suncore.storage.timeseries import TimeseriesProbe, probe_timeseries_file
from tests.api.conftest import client_for

NOW = datetime(2026, 11, 25, 10, 0, tzinfo=UTC)

MQTT_PASSWORD = "hunter2-mqtt-PASSWORD"
MQTT_USER = "mqtt_user_secret"
MQTT_HOST = "broker.private-home.example"
TIBBER_TOKEN = "tibber-TOKEN-5f0d9c1e77aa"
ENTRY_ID = "01HXCONFIGENTRYSECRET"
HOME_NAME = "Rosenweg 17"
LAT, LON = 52.520008, 13.404954
SECRETS = (MQTT_PASSWORD, MQTT_USER, MQTT_HOST, TIBBER_TOKEN, ENTRY_ID, HOME_NAME)


@pytest.fixture
def logging_ring() -> Iterator[LogRing]:
    """Real logging setup, restored afterwards."""
    root = logging.getLogger()
    saved, level = list(root.handlers), root.level
    LOG_RING.clear()
    configure_logging("info", stream=io.StringIO())
    try:
        yield LOG_RING
    finally:
        for handler in list(root.handlers):
            root.removeHandler(handler)
        for handler in saved:
            root.addHandler(handler)
        root.setLevel(level)
        LOG_RING.clear()


# --- pseudonyms -----------------------------------------------------------------------------


def test_pseudonyms_are_stable_within_an_export_and_differ_between_exports() -> None:
    one, other = Pseudonymizer(), Pseudonymizer()
    assert one.entity("sensor.pv_power") == one.entity("sensor.pv_power")
    assert one.entity("sensor.pv_power") != one.entity("sensor.house_power")
    assert one.entity("sensor.pv_power") != other.entity("sensor.pv_power")
    assert one.entity("sensor.pv_power").startswith("sensor.e_")  # the domain stays readable
    assert "pv_power" not in one.entity("sensor.pv_power")
    fixed = b"0123456789abcdef"
    assert Pseudonymizer(fixed).device("pool") == Pseudonymizer(fixed).device("pool")


def test_a_device_id_and_its_name_share_one_pseudonym() -> None:
    p = Pseudonymizer()
    token = p.register_device("pool_pump", "Pool pump")
    assert p.text("Pool pump failed; pool_pump retries") == f"{token} failed; {token} retries"
    assert p.text("POOL PUMP") == token  # names match regardless of case


def test_text_scrubs_known_and_unknown_entity_ids_secrets_and_coordinates() -> None:
    p = Pseudonymizer(secrets=[MQTT_PASSWORD], coordinates=[LAT, LON])
    p.register_area("Garden")
    p.register_ha_device("Heating rod controller")
    out = p.text(
        f"sensor.unknown_one and number.other_2 near Garden / Heating rod controller, "
        f"pw={MQTT_PASSWORD} at {LAT},{LON} (52.52 N)"
    )
    for real in ("unknown_one", "other_2", "Garden", "Heating rod", MQTT_PASSWORD, "52.52"):
        assert real not in out
    assert out.count("***") >= 3
    assert p.text("timeseries.open_failed safety.shadow_mode") == (
        "timeseries.open_failed safety.shadow_mode"
    )  # event names and reason keys are not entity ids


def test_a_registered_bearer_secret_is_masked_through_the_log_redactor() -> None:
    register_secret("supervisor-token-abcdef")
    try:
        text = Pseudonymizer().text("Authorization: Bearer abc.def.ghi and supervisor-token-abcdef")
    finally:
        DEFAULT_REGISTRY.clear()
    assert "abc.def.ghi" not in text
    assert "supervisor-token-abcdef" not in text


# --- the configuration allowlist --------------------------------------------------------------


def test_redact_config_applies_the_allowlist_rules() -> None:
    p = Pseudonymizer()
    p.register_device("pool", "Pool pump")
    sections = {
        "mqtt": {
            "schema_version": 1,
            "body": {
                "host": MQTT_HOST,
                "username": MQTT_USER,
                "password": "**********",
                "port": 1883,
                "tls": True,
                "broker": "manual",
            },
        },
        "site": {
            "body": {
                "latitude": LAT,
                "longitude": LON,
                "location": "Berlin",
                "pv": [{"name": "Roof south", "kwp": 9.8, "power": {"entity_id": "sensor.pv"}}],
            }
        },
        "operation": {"body": {"mode": "shadow", "device_modes": {"pool": "automatic"}}},
        "tariff": {
            "body": {
                "import": {
                    "kind": "dynamic",
                    "template_id": "tibber.price",
                    "series_key": HOME_NAME,
                    "action_params": {"token": TIBBER_TOKEN, "config_entry": ENTRY_ID, "n": 2},
                }
            }
        },
        "devices": {
            "body": {
                "devices": [
                    {
                        "id": "pool",
                        "name": "Pool pump",
                        "kind": "switch",
                        "free_text": "my secret note",
                        "entities": {"switch": {"entity_id": "switch.pool"}},
                    }
                ]
            }
        },
    }
    out, entities = redact_config(sections, p)
    text = json.dumps(out)
    for real in (
        MQTT_HOST,
        MQTT_USER,
        TIBBER_TOKEN,
        ENTRY_ID,
        HOME_NAME,
        "Roof south",
        "sensor.pv",
        "switch.pool",
        "Pool pump",
        "Berlin",
        "my secret note",
        '"pool"',
        str(LAT),
        str(LON),
    ):
        assert real not in text, real
    body = out["mqtt"]["body"]
    assert (body["host"], body["username"], body["password"]) == ("<set>", "<set>", "<masked>")
    assert (body["port"], body["tls"], body["broker"]) == (1883, True, "manual")
    assert out["site"]["body"]["pv"][0]["kwp"] == 9.8  # numbers are kept
    assert out["site"]["body"]["latitude"] == "<omitted>"
    imp = out["tariff"]["body"]["import"]
    assert imp["kind"] == "dynamic"
    assert imp["template_id"] == "tibber.price"  # a public template id is kept
    assert imp["series_key"] == "<omitted>"
    assert imp["action_params"] == {"token": "<masked>", "config_entry": "<omitted>", "n": 2}
    device = out["devices"]["body"]["devices"][0]
    assert device["id"] == device["name"] == p.device("pool")
    assert device["free_text"] == "<omitted>"
    assert list(out["operation"]["body"]["device_modes"]) == [p.device("pool")]
    assert set(entities) == {"sensor.pv", "switch.pool"}
    assert device["entities"]["switch"]["entity_id"] == entities["switch.pool"]


# --- decisions and logs ---------------------------------------------------------------------------


def _decision(n: int, asset: str = "pool_pump") -> Decision:
    intent = Intent(
        id=f"i{n}",
        ts=NOW - timedelta(minutes=n),
        asset_id=asset,
        action=SetpointAction(setpoint_c=21.0, base_c=22.0),
        reasons=(
            Reason(key="controller.surplus", args={"watts": 1200, "source": "sensor.sim_pv"}),
        ),
        origin=Origin.CONTROLLER,
    )
    return Decision(
        intent=intent,
        kind=DecisionKind.SHADOW,
        ts=NOW - timedelta(minutes=n),
        reasons=(
            Reason(key="safety.shadow_mode"),
            Reason(key="safety.stale", args={"entity": "sensor.sim_pv", "age_s": 400}),
        ),
        mode=OperatingMode.SHADOW,
    )


def _inputs(**over: Any) -> DiagnosticsInputs:
    fields: dict[str, Any] = {
        "now": NOW,
        "app_version": "1.2.3",
        "ha_core": "2026.11.1",
        "supervised": True,
        "demo": False,
        "operating_mode": "shadow",
        "config_export": {"db_schema_version": 6, "sections": {}},
    }
    fields.update(over)
    return DiagnosticsInputs(**fields)


def test_decisions_are_limited_and_use_the_same_pseudonyms_as_the_config() -> None:
    sections = {
        "devices": {
            "schema_version": 1,
            "body": {"devices": [{"id": "pool_pump", "name": "Pool pump", "kind": "switch"}]},
        },
        "site": {"body": {"house": {"entity_id": "sensor.sim_pv"}}},
    }
    decisions = [_decision(n) for n in range(DECISIONS_LIMIT + 50)]
    decisions.append(_decision(999, "battery"))
    doc = build_diagnostics(
        _inputs(
            config_export={"sections": sections},
            device_names={"pool_pump": "Pool pump"},
            decisions=decisions,
        )
    )
    assert len(doc.decisions) == DECISIONS_LIMIT
    config_device = doc.configuration["devices"]["body"]["devices"][0]
    assert doc.decisions[0].asset == config_device["id"]
    entity = doc.configuration["site"]["body"]["house"]["entity_id"]
    assert doc.decisions[0].reasons[1].args["entity"] == entity
    assert doc.decisions[0].intent_reasons[0].args["source"] == entity
    assert doc.decisions[0].reasons[1].args["age_s"] == 400
    assert doc.decisions[0].action == {"kind": "setpoint", "setpoint_c": 21.0, "base_c": 22.0}
    text = doc.model_dump_json()
    for real in ("pool_pump", "Pool pump", "sensor.sim_pv"):
        assert real not in text


def test_asset_ids_of_the_gate_and_mode_actions_survive() -> None:
    intent = Intent(
        id="i",
        ts=NOW,
        asset_id="battery",
        action=ModeAction(mode="heat"),
        origin=Origin.USER,
    )
    decision = Decision(
        intent=intent, kind=DecisionKind.ACCEPTED, ts=NOW, mode=OperatingMode.AUTOMATIC
    )
    doc = build_diagnostics(_inputs(decisions=[decision]))
    assert doc.decisions[0].asset == "battery"
    assert doc.decisions[0].action == {"kind": "mode", "mode": "heat"}
    switch = Decision(
        intent=intent.model_copy(update={"action": SwitchAction(on=True)}),
        kind=DecisionKind.SHADOW,
        ts=NOW,
        mode=OperatingMode.SHADOW,
    )
    assert build_diagnostics(_inputs(decisions=[switch])).decisions[0].action["on"] is True


def test_logs_are_redacted_and_limited(logging_ring: LogRing) -> None:
    log = structlog.get_logger("suncore.test")
    log.info("not.kept", detail="info lines are not kept")
    log.warning(
        "ha.something_failed",
        entity="sensor.sim_grid_power",
        error=f"Bearer abc.def and {MQTT_PASSWORD} at {LAT}",
        password="plain",
        latitude=LAT,
        count=3,
    )
    logging.getLogger("foreign.lib").error("Pool pump unreachable (%s)", "switch.sim_pool")
    try:
        raise RuntimeError(f"boom {TIBBER_TOKEN}")
    except RuntimeError:
        log.exception("planner.crashed")
    doc = build_diagnostics(
        _inputs(
            device_names={"pool_pump": "Pool pump"},
            secrets=[MQTT_PASSWORD, TIBBER_TOKEN],
            coordinates=[LAT, LON],
            logs=logging_ring.entries(),
        )
    )
    assert [line.level for line in doc.logs] == ["warning", "error", "error"]
    text = json.dumps([line.model_dump(mode="json") for line in doc.logs])
    for real in (
        "sensor.sim_grid_power",
        "abc.def",
        MQTT_PASSWORD,
        str(LAT),
        "plain",
        "Pool pump",
        "switch.sim_pool",
        TIBBER_TOKEN,
        "info lines",
    ):
        assert real not in text, real
    first = doc.logs[0]
    assert first.event == "ha.something_failed"
    assert first.fields["count"] == 3
    assert first.fields["password"] == "<masked>"
    assert first.fields["latitude"] == "<omitted>"
    assert doc.logs[2].exception is not None
    assert doc.logs[2].exception.startswith("RuntimeError: boom")


def test_the_ring_keeps_the_newest_500_warning_lines() -> None:
    ring = LogRing()
    logger = logging.getLogger("ring.test")
    logger.propagate = False
    logger.addHandler(ring)
    try:
        for i in range(LOG_RING_SIZE + 20):
            logger.warning("line %d", i)
        logger.info("never kept")
    finally:
        logger.removeHandler(ring)
    entries = ring.entries()
    assert len(entries) == LOG_RING_SIZE == LOG_LIMIT
    assert entries[0].event == "line 20"
    assert entries[-1].event == f"line {LOG_RING_SIZE + 19}"
    assert [e.event for e in ring.entries(2)] == ["line 518", "line 519"]


# --- the rest of the document -------------------------------------------------------------------


def test_missing_sources_are_null_and_named() -> None:
    doc = build_diagnostics(_inputs(ha_core=None))
    assert doc.versions.ha_core is None
    assert doc.planner.run is None
    assert doc.timeseries.state == "unavailable"
    assert doc.timeseries.size is None
    for path in ("versions.ha_core", "planner", "timeseries.size", "connection.registry"):
        assert path in doc.unavailable
    assert doc.versions.schemas["suncore_db"] == 6
    assert doc.versions.schemas["decision"] >= 2  # type: ignore[operator]


def test_the_too_new_timeseries_state_is_reported() -> None:
    probe = TimeseriesProbe("too_new", file_version=99, code_version=3)
    doc = build_diagnostics(_inputs(timeseries=probe, timeseries_open=False))
    assert doc.timeseries.state == "too_new"
    assert (doc.timeseries.file_schema_version, doc.timeseries.code_schema_version) == (99, 3)
    assert doc.timeseries.size is None
    ok = TimeseriesProbe("ok", 3, 3)
    assert build_diagnostics(_inputs(timeseries=ok, timeseries_open=True)).timeseries.state == "ok"
    assert (
        build_diagnostics(_inputs(timeseries=ok, timeseries_open=False)).timeseries.state
        == "unavailable"
    )


def test_probe_reads_a_file_without_touching_it(tmp_path: Path) -> None:
    path = tmp_path / "timeseries.db"
    assert probe_timeseries_file(path).state == "absent"
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA user_version = 9999")
    conn.execute("CREATE TABLE t (x)")
    conn.commit()
    conn.close()
    probe = probe_timeseries_file(path)
    assert (probe.state, probe.file_version) == ("too_new", 9999)
    assert probe.code_version < 9999
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA user_version = 1")
    conn.commit()
    conn.close()
    assert probe_timeseries_file(path).state == "ok"
    path.write_bytes(b"this is not a database" * 100)
    assert probe_timeseries_file(path).state == "unreadable"


def test_watchdog_scopes_and_mapped_entities_use_pseudonyms() -> None:
    sections = {"site": {"body": {"grid": {"power": {"entity_id": "sensor.sim_grid_power"}}}}}

    def facts(entity_id: str) -> EntityFacts:
        assert entity_id == "sensor.sim_grid_power"
        return EntityFacts(True, 3.5, False, "Living room", "SunCore demo house")

    doc = build_diagnostics(
        _inputs(
            config_export={"sections": sections},
            device_names={"pool_pump": "Pool pump"},
            entity_facts=facts,
            watchdog_connected=True,
            watchdog_scopes=[
                ScopeFacts("site", "normal", ()),
                ScopeFacts("pool_pump", "safe", ("stale", "jump")),
            ],
        )
    )
    assert doc.watchdog.scopes[0].scope == "site"
    assert doc.watchdog.scopes[1].scope.startswith("device_")
    assert doc.watchdog.scopes[1].faults == ["stale", "jump"]
    (mapped,) = doc.mapped_entities
    assert mapped.domain == "sensor"
    assert mapped.entity == doc.configuration["site"]["body"]["grid"]["power"]["entity_id"]
    assert (mapped.known, mapped.age_s, mapped.stale) == (True, 3.5, False)
    assert mapped.area is not None
    assert mapped.area.startswith("area_")
    text = doc.model_dump_json()
    assert "Living room" not in text
    assert "SunCore demo house" not in text


# --- the demo installation, seeded with secrets -----------------------------------------------


async def _seed_secrets(runtime: Runtime) -> None:
    await runtime.put_section(
        "mqtt",
        MqttConfig(
            broker="manual",
            host=MQTT_HOST,
            username=MQTT_USER,
            password=SecretStr(MQTT_PASSWORD),
        ),
    )
    tariff = await runtime.get_section("tariff")
    assert isinstance(tariff, TariffConfig)
    seeded: ImportTariff = tariff.import_.model_copy(
        update={
            "action_params": {"token": TIBBER_TOKEN, "config_entry": ENTRY_ID},
            "series_key": HOME_NAME,
        }
    )
    await runtime.put_section("tariff", tariff.model_copy(update={"import_": seeded}))
    runtime._ha_location = (LAT, LON)
    runtime.ha_core_version = "2026.11.2"


def _real_names(export: dict[str, Any], runtime: Runtime) -> set[str]:
    """Every id and name of the demo that must not leave the machine."""
    names: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in {"entity_id", "id", "name", "config_entry_id"} and isinstance(value, str):
                    names.add(value)
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(export["sections"])
    snapshot = runtime.catalogue.snapshot
    names.update(snapshot.entities)
    names.update(d.name for d in snapshot.devices.values() if d.name)
    names.update(snapshot.areas.values())
    names.update(d.id for d in (runtime.devices.devices if runtime.devices else []))
    return {n for n in names if len(n) >= 4}  # "PV" (the array's name) is checked in the config


async def test_the_demo_export_contains_no_secret_and_no_real_id(
    demo_runtime: Runtime, logging_ring: LogRing
) -> None:
    runtime = demo_runtime
    await _seed_secrets(runtime)
    log = structlog.get_logger("suncore.demo")
    log.warning(
        "ha.device_failed",
        device="Living room AC",
        entity="sensor.sim_grid_power",
        error=f"{MQTT_PASSWORD} {TIBBER_TOKEN} at {LAT}/{LON} in area Garden",
    )
    await runtime.decision_log.append(_decision(1, "living_room_ac"))
    export = await runtime.export_config()
    real = _real_names(export, runtime)
    assert {"living_room_ac", "Pool pump", "sensor.sim_grid_power", "Garden"} <= real

    async with client_for(create_app(runtime)) as client:
        response = await client.get("/api/v1/diagnostics")
    assert response.status_code == 200
    text = response.text
    lowered = text.lower()

    for secret in (*SECRETS, str(LAT), str(LON), "52.52", "13.4049"):
        assert secret.lower() not in lowered, secret
    for name in sorted(real):
        assert name.lower() not in lowered, name
    for word in ("latitude", "longitude", "password", "api_key"):
        # the keys exist (masked) or not at all; their values never do
        assert f'"{word}": "{MQTT_PASSWORD}"' not in text

    body = response.json()
    assert body["schema_version"] == 1
    assert body["versions"]["ha_core"] == "2026.11.2"
    assert body["versions"]["app"]
    assert body["architecture"]["demo"] is True
    assert body["architecture"]["machine"]
    assert body["connection"]["ha_state"] == "connected"
    assert body["watchdog"]["connected"] is True
    assert body["timeseries"]["state"] == "ok"
    assert body["timeseries"]["size"]["file_bytes"] > 0
    assert body["planner"]["run"] is not None
    assert body["decisions"]
    assert 1 <= len(body["decisions"]) <= 200
    config = body["configuration"]
    mqtt = config["mqtt"]["body"]
    assert (mqtt["host"], mqtt["username"], mqtt["password"]) == ("<set>", "<set>", "<masked>")
    imp = config["tariff"]["body"]["import"]
    assert imp["action_params"] == {"token": "<masked>", "config_entry": "<omitted>"}
    assert imp["series_key"] == "<omitted>"
    assert config["site"]["body"]["pv"][0]["name"].startswith("name_")

    # One pseudonym per real value across config, mapped entities, decisions and logs.
    devices = {d["id"] for d in config["devices"]["body"]["devices"]}
    assert all(d.startswith("device_") for d in devices)
    assert body["decisions"][0]["asset"] in devices or body["decisions"][0]["asset"].startswith(
        "device_"
    )
    ac = next(d for d in config["devices"]["body"]["devices"] if d["kind"] == "climate")
    ac_logged = next(line for line in body["logs"] if line["event"] == "ha.device_failed")
    assert ac_logged["fields"]["device"] == ac["id"] == ac["name"]
    grid = config["site"]["body"]["grid"]["power"]["entity_id"]
    assert ac_logged["fields"]["entity"] == grid
    assert grid in {m["entity"] for m in body["mapped_entities"]}
    assert {m["domain"] for m in body["mapped_entities"]} >= {"sensor"}
    assert any(m["area"] and m["area"].startswith("area_") for m in body["mapped_entities"])
    assert body["pseudonyms"]
    assert "versions.ha_core" not in body["unavailable"]


async def test_two_exports_use_different_pseudonyms(demo_runtime: Runtime) -> None:
    first = await demo_runtime.diagnostics()
    second = await demo_runtime.diagnostics()
    one = first.configuration["site"]["body"]["grid"]["power"]["entity_id"]
    two = second.configuration["site"]["body"]["grid"]["power"]["entity_id"]
    assert one != two
    assert one.startswith("sensor.e_")


async def test_the_diagnostics_call_changes_nothing(demo_runtime: Runtime) -> None:
    before = await demo_runtime.export_config()
    await demo_runtime.diagnostics()
    assert await demo_runtime.export_config() == before
