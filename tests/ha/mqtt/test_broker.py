# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from suncore.core.config import MqttConfig
from suncore.ha.mqtt import BrokerState, lookup_broker, supervisor_services_from_env
from suncore.ha.mqtt.broker import (
    ServiceNotEnabledError,
    SupervisorHttpServices,
    SupervisorUnreachableError,
    parse_service,
)
from suncore.ha.redact import DEFAULT_REGISTRY, redact_text

DATA = {
    "host": "core-mosquitto",
    "port": 1883,
    "ssl": False,
    "username": "homeassistant",
    "password": "s3cret",
    "protocol": "3.1.1",
    "addon": "core_mosquitto",
}


class FakeServices:
    def __init__(self, data: Mapping[str, Any] | Exception) -> None:
        self.data = data
        self.calls = 0

    async def mqtt_service(self) -> Mapping[str, Any]:
        self.calls += 1
        if isinstance(self.data, Exception):
            raise self.data
        return self.data


def test_parse_service_accepts_both_port_types_and_optional_credentials() -> None:
    s = parse_service({**DATA, "port": "8883", "ssl": True})
    assert (s.host, s.port, s.tls, s.username, s.password, s.protocol) == (
        "core-mosquitto",
        8883,
        True,
        "homeassistant",
        "s3cret",
        "3.1.1",
    )
    anonymous = parse_service({"host": "b", "port": 1883})
    assert (anonymous.username, anonymous.password, anonymous.tls) == (None, None, False)
    assert parse_service({"host": "b", "port": 1883, "protocol": "3.1"}).protocol == "3.1"


@pytest.mark.parametrize(
    "bad",
    [
        {},
        {"host": "", "port": 1},
        {"host": "b"},
        {"host": "b", "port": "x"},
        {"host": "b", "port": 0},
    ],
)
def test_parse_service_rejects_unusable_data(bad: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match=r"no |bad "):
        parse_service(bad)


def test_password_is_not_in_the_repr() -> None:
    assert "s3cret" not in repr(parse_service(DATA))


async def test_broker_passwords_are_registered_for_log_redaction() -> None:
    """Security review 1.0, SEC-02: the Supervisor's and a manual broker password are masked
    in every later log line, like the Supervisor token."""
    manual = MqttConfig(
        broker="manual",
        host="broker.lan",
        username="u",
        password="manual-pass-42",  # type: ignore[arg-type]
    )
    try:
        await lookup_broker(MqttConfig(), FakeServices(DATA))
        await lookup_broker(manual, None)
        out = redact_text("connect u:s3cret then u:manual-pass-42")
        assert "s3cret" not in out
        assert "manual-pass-42" not in out
    finally:
        DEFAULT_REGISTRY.clear()


async def test_supervisor_lookup_found() -> None:
    result = await lookup_broker(MqttConfig(), FakeServices(DATA))
    assert result.state is BrokerState.CONNECTING
    assert result.source == "supervisor"
    assert result.settings is not None
    assert result.settings.host == "core-mosquitto"


async def test_no_broker_app_gives_no_broker_with_reason() -> None:
    result = await lookup_broker(MqttConfig(), FakeServices(ServiceNotEnabledError("x")))
    assert result.state is BrokerState.NO_BROKER
    assert result.reason_key == "mqtt.reason.no_broker_service"
    assert result.settings is None


async def test_supervisor_failure_is_an_error_with_reason() -> None:
    result = await lookup_broker(MqttConfig(), FakeServices(SupervisorUnreachableError("x")))
    assert result.state is BrokerState.ERROR
    assert result.reason_key == "mqtt.reason.supervisor_unreachable"


async def test_unusable_supervisor_data_is_an_error() -> None:
    result = await lookup_broker(MqttConfig(), FakeServices({"host": ""}))
    assert result.state is BrokerState.ERROR


async def test_not_running_as_an_app_needs_a_manual_broker() -> None:
    result = await lookup_broker(MqttConfig(), None)
    assert result.state is BrokerState.ERROR
    assert result.reason_key == "mqtt.reason.not_supervised"


async def test_disabled_never_asks_the_supervisor() -> None:
    services = FakeServices(DATA)
    result = await lookup_broker(MqttConfig(enabled=False), services)
    assert result.state is BrokerState.DISABLED
    assert services.calls == 0


async def test_manual_settings_do_not_ask_the_supervisor() -> None:
    services = FakeServices(DATA)
    config = MqttConfig(
        broker="manual",
        host="broker.lan",
        port=8883,
        tls=True,
        username="u",
        password="p",  # type: ignore[arg-type]
    )
    result = await lookup_broker(config, services)
    assert services.calls == 0
    assert result.source == "manual"
    assert result.settings is not None
    assert (result.settings.host, result.settings.port, result.settings.tls) == (
        "broker.lan",
        8883,
        True,
    )
    assert (result.settings.username, result.settings.password) == ("u", "p")


async def test_manual_without_host_is_incomplete() -> None:
    # The model validator refuses this; the lookup is defensive anyway (a stored legacy row).
    result = await lookup_broker(MqttConfig.model_construct(broker="manual", host=None), None)
    assert result.state is BrokerState.ERROR
    assert result.reason_key == "mqtt.reason.manual_incomplete"


def test_supervisor_client_only_when_the_token_is_set() -> None:
    assert supervisor_services_from_env({}) is None
    assert supervisor_services_from_env({"SUPERVISOR_TOKEN": "  "}) is None
    assert isinstance(
        supervisor_services_from_env({"SUPERVISOR_TOKEN": "t"}), SupervisorHttpServices
    )


async def _server(handler: Any) -> TestServer:
    app = web.Application()
    app.router.add_get("/services/mqtt", handler)
    server = TestServer(app)
    await server.start_server()
    return server


async def test_http_client_sends_the_token_and_returns_data() -> None:
    seen: dict[str, str] = {}

    async def handler(request: web.Request) -> web.Response:
        seen["auth"] = request.headers.get("Authorization", "")
        return web.json_response({"result": "ok", "data": DATA})

    server = await _server(handler)
    try:
        client = SupervisorHttpServices("tok", url=str(server.make_url("/services/mqtt")))
        assert (await client.mqtt_service())["host"] == "core-mosquitto"
    finally:
        await server.close()
    assert seen["auth"] == "Bearer tok"


async def test_http_client_maps_service_not_enabled() -> None:
    async def handler(_: web.Request) -> web.Response:
        return web.json_response({"result": "error", "message": "Service not enabled"}, status=400)

    server = await _server(handler)
    try:
        client = SupervisorHttpServices("tok", url=str(server.make_url("/services/mqtt")))
        with pytest.raises(ServiceNotEnabledError):
            await client.mqtt_service()
    finally:
        await server.close()


@pytest.mark.parametrize(
    "response",
    [
        web.json_response({"result": "error", "message": "No access"}, status=403),
        web.Response(text="<html>", content_type="text/html"),
        web.json_response(["x"]),
    ],
)
async def test_http_client_maps_other_failures(response: web.Response) -> None:
    async def handler(_: web.Request) -> web.Response:
        return response

    server = await _server(handler)
    try:
        client = SupervisorHttpServices("tok", url=str(server.make_url("/services/mqtt")))
        with pytest.raises(SupervisorUnreachableError):
            await client.mqtt_service()
    finally:
        await server.close()


async def test_http_client_unreachable_supervisor() -> None:
    client = SupervisorHttpServices("tok", url="http://127.0.0.1:1/services/mqtt")
    with pytest.raises(SupervisorUnreachableError):
        await client.mqtt_service()
    assert aiohttp.ClientError  # the mapping covers connection errors, not just HTTP ones
