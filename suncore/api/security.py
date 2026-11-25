# SPDX-License-Identifier: Apache-2.0
"""Peer check for the Supervisor ingress proxy (pure ASGI, covers HTTP and WebSocket).

Under Supervisor the app listens on ``0.0.0.0:8099`` inside the container, but only the
ingress proxy at ``172.30.32.2`` may talk to it (``docs/research/ha-apps.md``, Ingress).
``/health`` is additionally open to loopback because the Docker ``HEALTHCHECK`` runs
inside the container.

Authorization note: this layer authenticates the *peer*, not the user. Write endpoints
(``PUT /api/v1/config/site``, ``POST /api/v1/config/import``) rely on the Supervisor
default ``panel_admin: true`` (omitted from ``config.yaml`` as a redundant default,
verified 2026-10-12), which restricts the whole ingress panel to admins.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any, Final

from suncore.api.services import PeerPolicy

Scope = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

_DENIED_BODY = b'{"detail":"api.error.forbidden"}'


class PeerCheckMiddleware:
    def __init__(self, app: ASGIApp, policy: PeerPolicy) -> None:
        self._app = app
        self._policy = policy

    def _allowed(self, scope: Scope) -> bool:
        policy = self._policy
        if not policy.enforce:
            return True
        client = scope.get("client")
        host = client[0] if client else None
        if host in policy.allowed_peers:
            return True
        return (
            scope["type"] == "http"
            and scope.get("method") == "GET"
            and scope.get("path") == "/health"
            and host in policy.health_local_peers
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket") or self._allowed(scope):
            await self._app(scope, receive, send)
            return
        if scope["type"] == "websocket":
            await receive()  # websocket.connect
            await send({"type": "websocket.close", "code": 1008})
            return
        await send(
            {
                "type": "http.response.start",
                "status": 403,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(_DENIED_BODY)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": _DENIED_BODY})


SECURITY_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"same-origin"),
)
"""Added to every HTTP response (security review 1.0, SEC-12). ``same-origin`` keeps the
ingress path, which carries the app's ingress token, out of the ``Referer`` of other origins.
The Supervisor ingress proxy passes response headers through (it filters only the transfer,
length, type and encoding headers; ``supervisor/api/ingress.py``, read 2026-11-25). A content
security policy is not set yet: it needs a check of the SPA and ECharts in a browser."""


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        async def with_headers(message: MutableMapping[str, Any]) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                present = {name.lower() for name, _ in headers}
                headers.extend(h for h in SECURITY_HEADERS if h[0] not in present)
                message["headers"] = headers
            await send(message)

        await self._app(scope, receive, with_headers)


REQUEST_HEADER: Final = "X-SunCore-Request"
"""Custom header every state-changing request must carry (security review 1.0, SEC-13).

A cross-site page can send a form post or a "simple" ``fetch`` without a CORS preflight, but
it cannot add a custom header without one, and SunCore answers no preflight. The SPA's fetch
helper sends ``X-SunCore-Request: 1`` on every request. Both ingress hops forward custom
request headers unchanged: HA Core's ``hassio/ingress.py`` drops only the length, encoding and
WebSocket handshake headers, and the Supervisor's ``api/ingress.py`` additionally only its own
token and remote-user headers (both read 2026-11-25)."""
REQUEST_HEADER_VALUE: Final = b"1"
SAFE_METHODS: Final = frozenset({"GET", "HEAD", "OPTIONS"})
STOP_EXEMPT: Final = ("POST", "/api/v1/control/emergency-stop")
"""The one route that works without the header: *setting* the emergency stop.

Safety argument (``control-safety``, 7-O): the stop is the "back to normal" action that must
work from any state, including from a client that is older than this check (a cached SPA, a
script, an automation's ``rest_command``). Setting it can only move SunCore towards the safe
state: every intent is rejected and devices are released to their baseline. A forged stop
costs optimisation, never hardware safety; the same action is open to every MQTT broker user
(SEC-14). *Clearing* the stop gives control back and is never exempt: the exemption is an
exact match of method and path, so ``/emergency-stop/clear`` (and any other suffix or a
trailing slash) needs the header."""

_MISSING_HEADER_BODY = b'{"detail":"api.error.request_header_missing"}'


def _requires_header(scope: Scope) -> bool:
    method = scope.get("method", "")
    if method in SAFE_METHODS:
        return False
    return (method, scope.get("path", "")) != STOP_EXEMPT


def _has_header(scope: Scope) -> bool:
    name = REQUEST_HEADER.lower().encode()
    return any(
        key.lower() == name and value.strip() == REQUEST_HEADER_VALUE
        for key, value in scope.get("headers", [])
    )


class RequireHeaderMiddleware:
    """Refuse every state-changing HTTP request without ``X-SunCore-Request: 1`` (403).

    It applies to every path, not only ``/api/v1``: today all writing routes are below
    ``/api/v1`` and nothing else accepts a write, so the wider scope only fails closed for a
    route added later. WebSocket handshakes are GETs and pass (the live socket only sends).
    """

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not _requires_header(scope) or _has_header(scope):
            await self._app(scope, receive, send)
            return
        await send(
            {
                "type": "http.response.start",
                "status": 403,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(_MISSING_HEADER_BODY)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": _MISSING_HEADER_BODY})
