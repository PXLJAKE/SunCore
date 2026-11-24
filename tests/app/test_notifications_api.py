# SPDX-License-Identifier: Apache-2.0
"""Notification settings, status and test through the API, inside the real runtime."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from suncore.api import create_app
from suncore.app.main import build_runtime
from suncore.app.runtime import Runtime
from suncore.app.settings import Settings
from suncore.core.decisions import Reason
from tests.api.conftest import client_for
from tests.app.conftest import RecordingConnection

TARGET = {"kind": "service", "id": "mobile_app_phone"}


@pytest.fixture
async def live(tmp_path: Path) -> AsyncIterator[tuple[Runtime, RecordingConnection]]:
    settings = Settings(
        demo_mode=False, data_dir=tmp_path / "data", static_dir=tmp_path / "no-dist"
    )
    conn = RecordingConnection(6000.0)
    runtime = await build_runtime(settings, env={}, connection=conn)
    await runtime.start()
    try:
        yield runtime, conn
    finally:
        await runtime.stop()


async def until(predicate: Any, timeout_s: float = 5.0) -> None:
    async with asyncio.timeout(timeout_s):
        while not predicate():  # noqa: ASYNC110 - polling a plain predicate
            await asyncio.sleep(0.01)


async def test_the_live_runtime_runs_the_service_and_the_demo_does_not(
    live: tuple[Runtime, RecordingConnection], demo_runtime: Runtime
) -> None:
    runtime, _ = live
    assert runtime.notifications is not None
    assert demo_runtime.notifications is None  # nothing leaves the demo
    assert demo_runtime.notifications_status().reason_key == "notify.reason.no_targets"
    assert (await demo_runtime.notifications_test()).sent == 0


async def test_settings_default_and_round_trip(
    live: tuple[Runtime, RecordingConnection],
) -> None:
    runtime, _ = live
    async with client_for(create_app(runtime)) as client:
        default = (await client.get("/api/v1/notifications/settings")).json()
        assert default["persistent"] is True
        assert default["targets"] == []
        assert default["categories"]["cheap_window"] is False
        assert default["quiet_hours"] == {"enabled": True, "start": "22:00", "end": "07:00"}
        assert default["daily_push_cap"] == 20
        body = {**default, "targets": [TARGET], "daily_push_cap": 5}
        put = await client.put("/api/v1/notifications/settings", json=body)
        assert put.status_code == 200
        assert (await client.get("/api/v1/notifications/settings")).json()["daily_push_cap"] == 5
        # the generic section route shows the same document
        assert (await client.get("/api/v1/config/notifications")).json()["targets"] == [TARGET]


@pytest.mark.parametrize(
    "target",
    [
        {"kind": "service", "id": "send_message"},
        {"kind": "service", "id": "persistent_notification"},
        {"kind": "service", "id": "notify.phone"},
        {"kind": "service", "id": "Mobile App"},
        {"kind": "entity", "id": "switch.pump"},
        {"kind": "entity", "id": "notify.x y"},
    ],
)
async def test_targets_that_could_switch_something_are_rejected(
    live: tuple[Runtime, RecordingConnection], target: dict[str, str]
) -> None:
    runtime, _ = live
    async with client_for(create_app(runtime)) as client:
        default = (await client.get("/api/v1/notifications/settings")).json()
        response = await client.put(
            "/api/v1/notifications/settings", json={**default, "targets": [target]}
        )
    assert response.status_code == 422


async def test_status_lists_notify_entities_and_the_reason(
    live: tuple[Runtime, RecordingConnection],
) -> None:
    runtime, _ = live
    await until(lambda: runtime.notifications_status().actions.value == "active")
    async with client_for(create_app(runtime)) as client:
        status = (await client.get("/api/v1/notifications/status")).json()
        assert status["actions"] == "active"
        assert status["reason_key"] == "notify.reason.no_targets"
        assert status["push_targets"] == 0
        assert isinstance(status["entity_targets"], list)
        default = (await client.get("/api/v1/notifications/settings")).json()
        await client.put("/api/v1/notifications/settings", json={**default, "targets": [TARGET]})
        await until(lambda: runtime.notifications_status().push_targets == 1)
        status = (await client.get("/api/v1/notifications/status")).json()
        assert (status["push_targets"], status["button_targets"]) == (1, 1)
        assert status["reason_key"] is None


async def test_the_test_message_goes_to_the_configured_target_only(
    live: tuple[Runtime, RecordingConnection],
) -> None:
    runtime, conn = live
    async with client_for(create_app(runtime)) as client:
        none = (await client.post("/api/v1/notifications/test")).json()
        assert none == {"sent": 0, "failed": 0, "reason_key": "notify.reason.no_targets"}
        default = (await client.get("/api/v1/notifications/settings")).json()
        await client.put("/api/v1/notifications/settings", json={**default, "targets": [TARGET]})
        await until(lambda: runtime.notifications_status().push_targets == 1)
        sent = (await client.post("/api/v1/notifications/test")).json()
    assert sent == {"sent": 1, "failed": 0, "reason_key": None}
    [record] = [r for r in conn.notify_log if r.service == "mobile_app_phone"]
    assert record.domain == "notify"
    assert record.data["title"] == "SunCore: test"


async def test_a_refused_mqtt_raise_becomes_a_persistent_notification(
    live: tuple[Runtime, RecordingConnection],
) -> None:
    runtime, conn = live
    runtime._on_mqtt_refusal(
        "auto", Reason(key="safety.mqtt_mode_raise_disabled", args={"entity": "auto"})
    )
    await until(lambda: bool(conn.notify_log))
    [record] = conn.notify_log
    assert (record.domain, record.service) == ("persistent_notification", "create")
    assert "Home Assistant (auto)" in record.data["message"]
    assert record.data["notification_id"].startswith("suncore_mqtt_raise_refused_auto")


async def test_importing_a_configuration_reloads_the_notification_settings(
    live: tuple[Runtime, RecordingConnection],
) -> None:
    runtime, _ = live
    document = await runtime.export_config()
    document["sections"] = {
        "notifications": {
            "schema_version": 1,
            "body": {"schema_version": 1, "targets": [TARGET], "daily_push_cap": 3},
        }
    }
    await runtime.import_config(document)
    await until(lambda: runtime.notifications_status().push_targets == 1)
