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
from typing import Any

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
