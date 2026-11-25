# SPDX-License-Identifier: Apache-2.0
"""Delivery of notifications through Home Assistant (ADR-0016 decision 1 and 7).

Three sinks, all behind ``HAConnection.notify`` and its allowlist:

* ``persistent_notification.create|dismiss`` (errors and safety events),
* ``notify.send_message`` for ``notify`` entities (message and title only: this action has no
  button field, so entity targets get the text and a pointer to the panel),
* the legacy ``notify.<service>`` for the companion app, with ``data.actions`` for buttons.

A delivery failure never raises: it is counted and logged without the payload.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import structlog

from suncore.core.config import NotifyTarget
from suncore.ha.connection import HAError

log = structlog.get_logger(__name__)

_ID_RE = re.compile(r"[^a-z0-9_]")


class NotifyPort(Protocol):
    """The part of ``HAConnection`` the sinks use."""

    async def notify(
        self,
        domain: str,
        service: str,
        data: Mapping[str, Any],
        target: Mapping[str, Any] | None = None,
    ) -> None: ...

    def set_notify_services(self, services: Collection[str]) -> None: ...


@dataclass(frozen=True, slots=True)
class Button:
    action: str
    title: str


@dataclass(frozen=True, slots=True)
class PushReport:
    sent: int
    failed: int
    with_buttons: int
    """Targets that received the buttons (legacy service targets only)."""


def persistent_id(key: str) -> str:
    """``notification_id`` of a dedupe key (one persistent notification per key)."""
    return "suncore_" + _ID_RE.sub("_", key.lower())[:80]


class HaNotificationSink:
    def __init__(self, port: NotifyPort) -> None:
        self._port = port

    def configure(self, targets: Sequence[NotifyTarget]) -> None:
        self._port.set_notify_services([t.id for t in targets if t.kind == "service"])

    async def persistent_create(self, key: str, title: str, message: str) -> bool:
        data = {"message": message, "title": title, "notification_id": persistent_id(key)}
        return await self._call("persistent_notification", "create", data)

    async def persistent_dismiss(self, key: str) -> bool:
        return await self._call(
            "persistent_notification", "dismiss", {"notification_id": persistent_id(key)}
        )

    async def push(
        self,
        targets: Sequence[NotifyTarget],
        title: str,
        message: str,
        *,
        buttons: Sequence[Button] = (),
        plain_message: str | None = None,
    ) -> PushReport:
        """Send to every target. ``plain_message`` (with the panel hint) goes to the targets
        that cannot carry ``buttons``; without buttons everybody gets ``message``."""
        sent = failed = with_buttons = 0
        for target in targets:
            if target.kind == "entity":
                text = plain_message if buttons and plain_message is not None else message
                ok = await self._call(
                    "notify",
                    "send_message",
                    {"message": text, "title": title},
                    {"entity_id": [target.id]},
                )
            else:
                data: dict[str, Any] = {"message": message, "title": title}
                if buttons:
                    data["data"] = {
                        "actions": [{"action": b.action, "title": b.title} for b in buttons]
                    }
                ok = await self._call("notify", target.id, data)
                with_buttons += int(ok and bool(buttons))
            sent += int(ok)
            failed += int(not ok)
        return PushReport(sent, failed, with_buttons)

    async def _call(
        self,
        domain: str,
        service: str,
        data: Mapping[str, Any],
        target: Mapping[str, Any] | None = None,
    ) -> bool:
        try:
            await self._port.notify(domain, service, data, target)
        except (HAError, TimeoutError) as exc:
            log.warning(
                "notify.delivery_failed", service=f"{domain}.{service}", error=type(exc).__name__
            )
            return False
        return True
