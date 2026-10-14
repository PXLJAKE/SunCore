# SPDX-License-Identifier: Apache-2.0
"""Demo mode end to end, without Home Assistant: runtime + API + live feed."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
import structlog.testing
from websockets.asyncio.client import ClientConnection, connect

from suncore.api import create_app
from suncore.app.main import build_runtime
from suncore.app.runtime import Runtime
from suncore.core.config import (
    SECTION_MODELS,
    DevicesConfig,
    ForecastConfig,
    OperationConfig,
    TariffConfig,
    WizardState,
)
from suncore.core.decisions import OperatingMode
from suncore.core.devices import (
    DeviceConfig,
    DeviceKind,
    OnOffControl,
    PowerModel,
    PowerModelKind,
)
from suncore.core.site import EntityRef
from suncore.ha.connection import CommandTimeoutError
from suncore.sim.demo_config import WIZARD_STEPS
from suncore.sim.demo_devices import AC_ID
from suncore.sim.fake_ha import SIM_ENTITY_IDS, FakeHAConnection, demo_site_config
from suncore.templates import DeviceTemplate
from tests.api.conftest import client_for, serving
from tests.app.conftest import RecordingConnection, demo_settings


async def _recv(ws: ClientConnection, wait_s: float = 5.0) -> dict[str, Any]:
    return json.loads(await asyncio.wait_for(ws.recv(), wait_s))  # type: ignore[no-any-return]


async def _wait_for_flows(runtime: Runtime) -> None:
    for _ in range(300):
        flows = runtime.flows()
        if flows is not None and flows.pv_w.value is not None and flows.grid_w.value is not None:
            return
        await asyncio.sleep(0.01)
    pytest.fail("no flows arrived")


async def test_demo_health_and_info(demo_runtime: Runtime) -> None:
    await _wait_for_flows(demo_runtime)
    async with client_for(create_app(demo_runtime)) as client:
        health = await client.get("/health")
        info = await client.get("/api/v1/info")
        site = await client.get("/api/v1/config/site")
        entities = await client.get("/api/v1/entities")
    assert health.status_code == 200
    body = health.json()
    assert body["ha_state"] == "connected"
    assert body["db_ok"] is True
    assert body["ha_last_update_age_s"] is not None
    assert info.json()["demo"] is True
    assert info.json()["mode"] == "shadow"
    assert site.json() == demo_site_config().model_dump(mode="json")
    # The picker offers power and battery sensors: the site sensors, the pool pump plug and the
    # polled power sensor of the second AC integration.
    assert {c["entity_id"] for c in entities.json()} == {
        *SIM_ENTITY_IDS,
        "sensor.sim_pool_pump_power",
        "sensor.sim_living_room_ac_energy_current",
    }


async def test_demo_live_feed_balance_closes(demo_runtime: Runtime) -> None:
    app = create_app(demo_runtime, live_min_interval_s=0.02, live_health_interval_s=30)
    async with serving(app) as port, connect(f"ws://127.0.0.1:{port}/api/v1/live") as ws:
        messages = [await _recv(ws)]
        while len(messages) < 8:
            messages.append(await _recv(ws))
    assert messages[0]["type"] == "snapshot"
    assert messages[0]["health"]["ok"] is True
    flow_msgs = [m for m in messages if m["type"] == "flows" and m["flows"] is not None]
    assert flow_msgs, messages
    seen_values = set()
    for m in flow_msgs:
        f = m["flows"]
        for key in ("grid_w", "pv_w", "battery_w", "house_w"):
            assert f[key]["quality"] == "ok", (key, f)
        # Core convention: pv + grid == house + battery (values are rounded to 0.1 W).
        residual = f["pv_w"]["value"] + f["grid_w"]["value"] - f["battery_w"]["value"]
        assert residual == pytest.approx(f["house_w"]["value"], abs=0.5)
        assert 0.0 <= f["battery_soc_pct"]["value"] <= 100.0
        assert f["pv_w"]["value"] >= 0.0
        seen_values.add(f["house_w"]["value"])
    assert len(seen_values) > 1  # the simulation advances


DEMO_DEVICE_ENTITIES = frozenset(
    {
        "climate.sim_living_room_ac",
        "sensor.sim_living_room_ac_energy_today",
        "sensor.sim_living_room_ac_energy_yesterday",
        "sensor.sim_living_room_ac_energy_current",
        "switch.sim_pool_pump",
        "sensor.sim_pool_pump_power",
        "number.sim_heating_rod_limit",
    }
)
"""The demo starts with devices; the runtime subscribes to their entities too."""
DEMO_SUBSCRIBED = frozenset(SIM_ENTITY_IDS) | DEMO_DEVICE_ENTITIES


async def test_demo_starts_fully_configured(demo_runtime: Runtime) -> None:
    sections = await demo_runtime.config.stored_sections()
    assert sections == frozenset(SECTION_MODELS)
    wizard = await demo_runtime.get_section("wizard")
    assert isinstance(wizard, WizardState)
    assert wizard.completed
    assert wizard.completed_steps == list(WIZARD_STEPS)
    devices = await demo_runtime.get_section("devices")
    assert isinstance(devices, DevicesConfig)
    assert [d.id for d in devices.by_priority()] == ["pool_pump", "living_room_ac", "heating_rod"]
    assert {d.template_id for d in devices.devices} == {
        "lg.thinq_climate",
        "generic.switch",
        "generic.number_load",
    }
    tariff = await demo_runtime.get_section("tariff")
    assert isinstance(tariff, TariffConfig)
    assert tariff.import_.kind == "dynamic"
    assert tariff.import_.price_entity == EntityRef(entity_id="sensor.sim_electricity_price")
    forecast = await demo_runtime.get_section("forecast")
    assert isinstance(forecast, ForecastConfig)
    assert forecast.forecast_template_ids == ["solcast.pv"]
    operation = await demo_runtime.get_section("operation")
    assert isinstance(operation, OperationConfig)
    assert operation.mode is OperatingMode.SHADOW
    assert demo_runtime.gate.mode is OperatingMode.SHADOW
    items = await demo_runtime.system_check(None)
    assert not [i for i in items if i.reason.key.startswith("wizard.check.section_missing")]
    assert not [i for i in items if i.reason.key == "wizard.check.forecast_missing"]


async def test_demo_devices_follow_the_templates(demo_runtime: Runtime) -> None:
    devices = await demo_runtime.get_section("devices")
    assert isinstance(devices, DevicesConfig)
    by_id = {d.id: d for d in devices.devices}
    ac, pump, rod = by_id["living_room_ac"], by_id["pool_pump"], by_id["heating_rod"]
    lg = demo_runtime.template_catalog.get("lg.thinq_climate")
    assert isinstance(lg, DeviceTemplate)
    assert ac.settle_s == lg.defaults.settle_s
    assert ac.min_run_s == lg.defaults.min_run_s
    # The power sensor of the second integration (smartthinq-like device) is mapped: measured.
    assert ac.power_model.kind.value == "measured"
    assert ac.power_model.power_role == "power"
    assert ac.entities["power"].entity_id == "sensor.sim_living_room_ac_energy_current"
    assert ac.power_model.energy_role is None
    assert ac.control.role == "climate"
    assert ac.entities["climate"].entity_id == AC_ID
    assert ac.comfort is not None
    assert pump.power_model.kind.value == "measured"
    assert pump.power_model.power_role == "power"
    assert rod.control.kind == "continuous"
    assert rod.hysteresis_w == 100
    # Every mapped entity exists in the simulator registry.
    known = {m.entity_id for m in demo_runtime.connection.entity_metas()}  # type: ignore[attr-defined]
    assert {r.entity_id for r in devices.entity_refs()} <= known


async def test_put_site_resubscribes(demo_runtime: Runtime) -> None:
    conn = demo_runtime.connection
    assert isinstance(conn, RecordingConnection)
    assert conn.subscriptions[-1] == DEMO_SUBSCRIBED
    reduced = demo_site_config().model_copy(update={"house": None})
    async with client_for(create_app(demo_runtime)) as client:
        r = await client.put("/api/v1/config/site", json=reduced.model_dump(mode="json"))
        stored = await client.get("/api/v1/config/site")
    assert r.status_code == 200
    assert len(conn.subscriptions) >= 2
    assert conn.subscriptions[-1] == DEMO_SUBSCRIBED - {"sensor.sim_house_power"}
    assert stored.json()["house"] is None
    assert (await demo_runtime.config.get_site()) == reduced


@pytest.mark.parametrize(
    "error", [CommandTimeoutError("no initial event"), TimeoutError()], ids=["wrapped", "bare"]
)
async def test_put_site_survives_subscribe_timeout(
    demo_runtime: Runtime, error: BaseException
) -> None:
    conn = demo_runtime.connection
    assert isinstance(conn, RecordingConnection)
    conn.subscribe_error = error
    reduced = demo_site_config().model_copy(update={"house": None})
    with structlog.testing.capture_logs() as logs:
        async with client_for(create_app(demo_runtime)) as client:
            r = await client.put("/api/v1/config/site", json=reduced.model_dump(mode="json"))
    assert r.status_code == 200
    assert (await demo_runtime.config.get_site()) == reduced  # persisted
    assert [e["event"] for e in logs if e["log_level"] == "warning"] == ["ha.subscribe_failed"]


async def test_put_site_notifies_live_listeners(demo_runtime: Runtime) -> None:
    hits: list[int] = []
    remove = demo_runtime.add_change_listener(lambda: hits.append(1))
    await demo_runtime.put_site(demo_site_config())
    remove()
    assert hits


async def test_export_import_roundtrip_resubscribes(demo_runtime: Runtime) -> None:
    conn = demo_runtime.connection
    assert isinstance(conn, RecordingConnection)
    async with client_for(create_app(demo_runtime)) as client:
        exported = (await client.get("/api/v1/config/export")).json()
        assert (
            exported["sections"]["site"]["body"]["pv"][0]["power"]["entity_id"]
            == "sensor.sim_pv_power"
        )
        exported["sections"]["site"]["body"]["pv"] = [
            {"name": "Roof", "power": EntityRef(entity_id="sensor.sim_pv_power").model_dump()}
        ]
        before = len(conn.subscriptions)
        r = await client.post("/api/v1/config/import", json=exported)
        bad = await client.post("/api/v1/config/import", json={"nope": 1})
    assert r.status_code == 200
    assert r.json() == {"imported_sections": sorted(SECTION_MODELS)}
    assert len(conn.subscriptions) == before + 1
    assert bad.status_code == 422


async def test_import_over_real_limit_is_413(demo_runtime: Runtime) -> None:
    limit = demo_runtime.max_import_bytes
    async with client_for(create_app(demo_runtime)) as client:
        r = await client.post("/api/v1/config/import", content=b" " * (limit + 1))
    assert r.status_code == 413


async def test_decisions_endpoint_reads_log(demo_runtime: Runtime) -> None:
    from suncore.core.decisions import Intent, Origin, SwitchAction  # noqa: PLC0415

    gate = demo_runtime.gate
    decision = await gate.submit(
        Intent(
            id="x1",
            ts=demo_runtime.clock(),
            asset_id="asset.test",
            action=SwitchAction(on=True),
            origin=Origin.USER,
        )
    )
    async with client_for(create_app(demo_runtime)) as client:
        r = await client.get("/api/v1/decisions?limit=10")
    body = r.json()
    assert [d["intent"]["id"] for d in body] == ["x1"]
    assert body[0]["kind"] == decision.kind.value == "shadow"


async def test_health_stays_200_while_connection_is_down(tmp_path: Path) -> None:
    conn = RecordingConnection(speed=6000.0)
    runtime = await build_runtime(demo_settings(tmp_path), env={}, connection=conn)
    try:
        # Not started: the fake connection is still DISCONNECTED.
        async with client_for(create_app(runtime)) as client:
            r = await client.get("/health")
        assert r.status_code == 200  # liveness: HA being away does not fail the watchdog
        assert r.json()["ha_state"] == "disconnected"
        assert r.json()["ok"] is True
    finally:
        await runtime.stop()


async def test_demo_does_not_touch_data_dir(tmp_path: Path) -> None:
    settings = demo_settings(tmp_path)
    runtime = await build_runtime(settings, env={}, connection=RecordingConnection(6000.0))
    await runtime.stop()
    assert not settings.data_dir.exists()


async def test_peer_policy_follows_environment(tmp_path: Path) -> None:
    conn = RecordingConnection(6000.0)
    supervised = await build_runtime(
        demo_settings(tmp_path), env={"SUPERVISOR_TOKEN": "secret-token-1"}, connection=conn
    )
    try:
        assert supervised.peer_policy.enforce is True
        assert supervised.peer_policy.allowed_peers == ("172.30.32.2",)
    finally:
        await supervised.stop()
    override = await build_runtime(
        demo_settings(tmp_path, allow_any_peer=True),
        env={"SUPERVISOR_TOKEN": "secret-token-1"},
        connection=RecordingConnection(6000.0),
    )
    try:
        assert override.peer_policy.enforce is False
    finally:
        await override.stop()
    plain = await build_runtime(
        demo_settings(tmp_path), env={}, connection=RecordingConnection(6000.0)
    )
    try:
        assert plain.peer_policy.enforce is True
        assert plain.peer_policy.allowed_peers == ("127.0.0.1", "::1")
    finally:
        await plain.stop()


async def test_demo_starts_at_the_configured_time_of_day(tmp_path: Path) -> None:
    runtime = await build_runtime(demo_settings(tmp_path, demo_start_time="12:00"), env={})
    try:
        await runtime.start()
        conn = runtime.connection
        assert isinstance(conn, FakeHAConnection)
        for _ in range(100):
            if conn.sim_time is not None:
                break
            await asyncio.sleep(0.01)
        assert conn.sim_time is not None
        assert conn.sim_time.hour == 12
    finally:
        await runtime.stop()


# --- generic sections (WP4) -------------------------------------------------------


def _switch(dev_id: str) -> DeviceConfig:
    return DeviceConfig(
        id=dev_id,
        template_id="generic.switch",
        name=dev_id,
        kind=DeviceKind.SWITCH,
        entities={
            "switch": EntityRef(entity_id=f"switch.{dev_id}"),
            "power": EntityRef(entity_id=f"sensor.{dev_id}_power"),
        },
        power_model=PowerModel(kind=PowerModelKind.MEASURED, power_role="power"),
        control=OnOffControl(kind="on_off", role="switch"),
    )


async def test_put_devices_resubscribes_and_shadows_new_ones(demo_runtime: Runtime) -> None:
    conn = demo_runtime.connection
    assert isinstance(conn, RecordingConnection)
    auto = OperatingMode.AUTOMATIC
    await demo_runtime.put_section("operation", OperationConfig(device_modes={"heater": auto}))
    stored = await demo_runtime.put_section("devices", DevicesConfig(devices=[_switch("heater")]))
    assert isinstance(stored, DevicesConfig)
    assert await demo_runtime.get_section("operation") == OperationConfig()
    assert conn.subscriptions[-1] == frozenset(SIM_ENTITY_IDS) | {
        "switch.heater",
        "sensor.heater_power",
    }
    assert await demo_runtime.get_section("devices") == stored


async def test_other_sections_do_not_resubscribe_nor_touch_the_gate(demo_runtime: Runtime) -> None:
    conn = demo_runtime.connection
    assert isinstance(conn, RecordingConnection)
    before = len(conn.subscriptions)
    assert await demo_runtime.get_section("operation") == OperationConfig()
    await demo_runtime.put_section(
        "operation",
        OperationConfig(mode=OperatingMode.AUTOMATIC, device_modes={"x": OperatingMode.AUTOMATIC}),
    )
    await demo_runtime.put_section("wizard", WizardState(completed=True))
    assert len(conn.subscriptions) == before
    assert demo_runtime.mode == "shadow"
    assert demo_runtime.gate.mode is OperatingMode.SHADOW


async def test_import_reloads_devices(demo_runtime: Runtime) -> None:
    conn = demo_runtime.connection
    assert isinstance(conn, RecordingConnection)
    await demo_runtime.put_section("devices", DevicesConfig(devices=[_switch("heater")]))
    exported = await demo_runtime.export_config()
    exported["sections"]["devices"]["body"]["devices"] = []
    await demo_runtime.import_config(exported)
    assert demo_runtime.devices == DevicesConfig()
    assert conn.subscriptions[-1] == frozenset(SIM_ENTITY_IDS)
