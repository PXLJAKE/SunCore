# SPDX-License-Identifier: Apache-2.0
"""Streaming request body caps (pure ASGI).

``Content-Length`` is only a claim: a client can omit it (chunked) or lie. The middleware
counts the bytes that actually arrive and answers ``413 api.error.body_too_large`` as soon as
a cap is exceeded. FastAPI reads the whole body before a handler or dependency runs, so the
cap has to sit in front of it. The config import endpoint counts its own stream (it has a
configurable, larger limit).
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any, Final

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

BODY_TOO_LARGE_BODY: Final = b'{"detail":"api.error.body_too_large"}'


class BodyLimitMiddleware:
    """Cap the request body of selected ``(method, path pattern)`` routes."""

    def __init__(self, app: ASGIApp, rules: list[tuple[str, re.Pattern[str], int]]) -> None:
        self._app = app
        self._rules = rules

    def _limit(self, scope: Scope) -> int | None:
        method, path = scope.get("method"), scope.get("path", "")
        for rule_method, pattern, limit in self._rules:
            if method == rule_method and pattern.fullmatch(path):
                return limit
        return None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        limit = self._limit(scope) if scope["type"] == "http" else None
        if limit is None:
            await self._app(scope, receive, send)
            return
        declared = dict(scope.get("headers", [])).get(b"content-length", b"")
        if declared.isdigit() and int(declared) > limit:
            await _reject(send)
            return

        received = 0
        exceeded = False
        responded = False

        async def counting_receive() -> Message:
            nonlocal received, exceeded
            if exceeded:
                return {"type": "http.request", "body": b"", "more_body": False}
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    exceeded = True  # the app sees an empty body; its answer is replaced below
                    return {"type": "http.request", "body": b"", "more_body": False}
            return message

        async def guarded_send(message: Message) -> None:
            nonlocal responded
            if not exceeded:
                await send(message)
                return
            if not responded:
                responded = True
                await _reject(send)

        await self._app(scope, counting_receive, guarded_send)


async def _reject(send: Send) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(BODY_TOO_LARGE_BODY)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": BODY_TOO_LARGE_BODY})
