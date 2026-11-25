# SPDX-License-Identifier: Apache-2.0
"""Peer check: ingress proxy only, /health open to loopback, dev override."""

from __future__ import annotations

import json
import re

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus

from suncore.api import PeerPolicy, create_app
from suncore.api.app import BODY_LIMITS, BODY_MAX_BYTES
from suncore.api.security import SAFE_METHODS, STOP_EXEMPT
from suncore.app.settings import Settings
from tests.api.conftest import FakeServices, client_for, serving

INGRESS = ("172.30.32.2", 40000)
OTHER = ("172.30.32.5", 40000)
LOOPBACK = ("127.0.0.1", 40000)


@pytest.fixture
def enforced(services: FakeServices) -> FakeServices:
    services.peer_policy = PeerPolicy(enforce=True)
    return services


async def test_ingress_peer_allowed(enforced: FakeServices) -> None:
    async with client_for(create_app(enforced), INGRESS) as client:
        assert (await client.get("/api/v1/info")).status_code == 200
        assert (await client.get("/health")).status_code == 200


@pytest.mark.parametrize("path", ["/api/v1/info", "/api/v1/config/export", "/openapi.json", "/"])
async def test_other_peer_denied(enforced: FakeServices, path: str) -> None:
    async with client_for(create_app(enforced), OTHER) as client:
        r = await client.get(path)
    assert r.status_code == 403
    assert r.json() == {"detail": "api.error.forbidden"}


async def test_write_from_other_peer_denied(enforced: FakeServices) -> None:
    async with client_for(create_app(enforced), OTHER) as client:
        r = await client.put("/api/v1/config/site", json={})
    assert r.status_code == 403
    assert enforced.site is None


async def test_health_exempt_only_for_loopback(enforced: FakeServices) -> None:
    async with client_for(create_app(enforced), LOOPBACK) as client:
        assert (await client.get("/health")).status_code == 200
        assert (await client.get("/api/v1/info")).status_code == 403
    async with client_for(create_app(enforced), OTHER) as client:
        assert (await client.get("/health")).status_code == 403


async def test_health_loopback_ipv6(enforced: FakeServices) -> None:
    async with client_for(create_app(enforced), ("::1", 1)) as client:
        assert (await client.get("/health")).status_code == 200


async def test_missing_client_is_denied(enforced: FakeServices) -> None:
    async with client_for(create_app(enforced), None) as client:  # type: ignore[arg-type]
        assert (await client.get("/api/v1/info")).status_code == 403


async def test_dev_override_allows_everyone(services: FakeServices) -> None:
    services.peer_policy = PeerPolicy(enforce=False)
    async with client_for(create_app(services), OTHER) as client:
        assert (await client.get("/api/v1/info")).status_code == 200


async def test_websocket_denied_for_other_peer(services: FakeServices) -> None:
    services.peer_policy = PeerPolicy(enforce=True, allowed_peers=("10.9.9.9",))
    async with serving(create_app(services)) as port:
        with pytest.raises(InvalidStatus) as exc:
            async with connect(f"ws://127.0.0.1:{port}/api/v1/live"):
                pass
    assert exc.value.response.status_code == 403


async def test_websocket_allowed_for_ingress_peer(services: FakeServices) -> None:
    services.peer_policy = PeerPolicy(enforce=True, allowed_peers=("127.0.0.1",))
    async with (
        serving(create_app(services)) as port,
        connect(f"ws://127.0.0.1:{port}/api/v1/live") as ws,
    ):
        assert json.loads(await ws.recv())["type"] == "snapshot"


# --- security review 1.0 (docs/security/review-1.0.md) ------------------------------------


async def test_security_headers_on_answers_and_refusals(enforced: FakeServices) -> None:
    """SEC-12: nosniff and the referrer policy on a normal answer and on the peer refusal."""
    for peer, status in ((INGRESS, 200), (OTHER, 403)):
        async with client_for(create_app(enforced), peer) as client:
            r = await client.get("/api/v1/info")
        assert r.status_code == status
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["referrer-policy"] == "same-origin"


def _concrete(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "x", path)


def test_every_json_body_route_has_a_body_cap(services: FakeServices) -> None:
    """SEC-06: a route that parses a JSON body is capped before FastAPI reads it. The config
    import reads its own stream against ``max_import_bytes`` and has no body field."""
    app = create_app(services)
    uncapped = []
    for route in app.routes:
        if not isinstance(route, APIRoute) or route.body_field is None:
            continue
        path = _concrete(route.path)
        for method in sorted(route.methods):
            if not any(m == method and p.fullmatch(path) for m, p, _ in BODY_LIMITS):
                uncapped.append(f"{method} {route.path}")
    assert uncapped == []


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("PUT", "/api/v1/notifications/settings"),
        ("POST", "/api/v1/devices/heater/override"),
        ("PUT", "/api/v1/operation/mode"),
        ("PUT", "/api/v1/operation/devices/heater"),
        ("PUT", "/api/v1/operation/battery"),
        ("PUT", "/api/v1/operation/inverter"),
        ("POST", "/api/v1/backtests"),
    ],
)
async def test_oversized_bodies_are_refused_before_parsing(
    services: FakeServices, method: str, path: str
) -> None:
    body = b'{"x":"' + b"a" * BODY_MAX_BYTES + b'"}'
    async with client_for(create_app(services)) as client:
        r = await client.request(
            method, path, content=body, headers={"content-type": "application/json"}
        )
    assert (r.status_code, r.json()) == (413, {"detail": "api.error.body_too_large"})


def test_server_ignores_forwarded_headers(services: FakeServices) -> None:
    """SEC-05: the peer check must see the TCP peer, never an X-Forwarded-For value."""
    from suncore.app.main import make_server  # noqa: PLC0415

    services.settings = Settings(host="127.0.0.1", port=8099)  # type: ignore[attr-defined]
    server = make_server(services)  # type: ignore[arg-type]
    assert server.config.proxy_headers is False


# --- SEC-13: custom request header on every write ------------------------------------------

STOP = "/api/v1/control/emergency-stop"
MISSING = {"detail": "api.error.request_header_missing"}


def _write_routes(app: FastAPI) -> list[tuple[str, str]]:
    return sorted(
        (method, _concrete(route.path))
        for route in app.routes
        if isinstance(route, APIRoute)
        for method in route.methods
        if method not in SAFE_METHODS
    )


def test_the_stop_exemption_names_a_real_route(services: FakeServices) -> None:
    """The exemption is an exact (method, path); a renamed route must not silently lose it."""
    assert STOP_EXEMPT == ("POST", STOP)
    assert STOP_EXEMPT in _write_routes(create_app(services))


def test_every_write_route_is_under_the_api_prefix(services: FakeServices) -> None:
    routes = _write_routes(create_app(services))
    assert routes, "no write routes found"
    assert [r for r in routes if not r[1].startswith("/api/v1/")] == []


async def test_every_write_without_the_header_is_refused_except_setting_the_stop(
    services: FakeServices,
) -> None:
    app = create_app(services)
    refused, passed = [], []
    async with client_for(app, request_header=False) as client:
        for method, path in _write_routes(app):
            r = await client.request(method, path, json={})
            (passed if r.status_code != 403 or r.json() != MISSING else refused).append(
                (method, path)
            )
    assert passed == [STOP_EXEMPT]
    assert len(refused) == len(_write_routes(app)) - 1
    # Nothing reached a service except the stop itself.
    assert [c[0] for c in services.calls] == ["emergency_stop"]


async def test_reads_need_no_header(services: FakeServices) -> None:
    async with client_for(create_app(services), request_header=False) as client:
        assert (await client.get("/api/v1/control/state")).status_code == 200
        assert (await client.head("/health")).status_code in (200, 405)
        assert (await client.options("/api/v1/info")).status_code != 403


async def test_a_stale_client_can_always_set_the_stop(services: FakeServices) -> None:
    """Setting the stop moves towards the safe state; no header, any content type."""
    async with client_for(create_app(services), request_header=False) as client:
        bare = await client.post(STOP)
        form = await client.post(STOP, data={"a": "b"})
        state = await client.get("/api/v1/control/state")
    assert (bare.status_code, bare.json()) == (200, {"active": True})
    assert form.status_code == 200
    assert state.json()["emergency_stop"] is True


async def test_clearing_the_stop_is_never_exempt(services: FakeServices) -> None:
    app = create_app(services)
    async with client_for(app) as client:
        await client.post(STOP)
    async with client_for(app, request_header=False) as client:
        for variant in (
            f"{STOP}/clear",
            f"{STOP}/clear/",
            f"{STOP}/",
            f"{STOP}/x/../clear",
        ):
            r = await client.post(variant)
            assert (r.status_code, r.json()) == (403, MISSING), variant
        state = await client.get("/api/v1/control/state")
    assert state.json()["emergency_stop"] is True
    assert [c[0] for c in services.calls] == ["emergency_stop"]
    async with client_for(app) as client:
        assert (await client.post(f"{STOP}/clear")).json() == {"active": False}


@pytest.mark.parametrize("value", ["0", "", "true", "11"])
async def test_only_the_exact_header_value_counts(services: FakeServices, value: str) -> None:
    async with client_for(create_app(services), request_header=False) as client:
        r = await client.post("/api/v1/plan/replan", headers={"X-SunCore-Request": value})
    assert (r.status_code, r.json()) == (403, MISSING)


@pytest.mark.parametrize("name", ["X-SunCore-Request", "x-suncore-request", "X-SUNCORE-REQUEST"])
async def test_header_name_is_case_insensitive(services: FakeServices, name: str) -> None:
    async with client_for(create_app(services), request_header=False) as client:
        r = await client.post(f"{STOP}/clear", headers={name: "1"})
    assert r.status_code == 200


async def test_the_peer_check_runs_before_the_header_check(enforced: FakeServices) -> None:
    async with client_for(create_app(enforced), OTHER, request_header=False) as client:
        r = await client.post(STOP)
    assert (r.status_code, r.json()) == (403, {"detail": "api.error.forbidden"})
    assert enforced.calls == []
