# SPDX-License-Identifier: Apache-2.0
"""Transports: where and how to reach the Home Assistant WebSocket API.

Both transports speak the same protocol (auth handshake with ``access_token``); they
only differ in URL and token source (ADR-0010):

* :class:`SupervisorTransport` - ``ws://supervisor/core/websocket`` with the app's
  ``SUPERVISOR_TOKEN`` (``docs/research/ha-apps.md``, "Communication with Supervisor and
  Core"; requires ``homeassistant_api: true`` in the app config).
* :class:`DirectTransport` - a Home Assistant URL plus a long-lived access token
  (devcontainer, tests, a later standalone container). The WebSocket URL is derived as
  ``<base>/api/websocket`` with ``http`` -> ``ws`` and ``https`` -> ``wss``; endpoint path
  verified 2026-10-12 in ``developers.home-assistant`` ``docs/api/websocket.md`` (via
  ``docs/research/ha-websocket-api.md``).

Tokens are excluded from ``repr`` so they cannot leak through logging of the object.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Final, Protocol
from urllib.parse import urlsplit, urlunsplit

SUPERVISOR_WS_URL: Final = "ws://supervisor/core/websocket"
ENV_SUPERVISOR_TOKEN: Final = "SUPERVISOR_TOKEN"
ENV_HA_URL: Final = "SUNCORE_HA_URL"
ENV_HA_TOKEN: Final = "SUNCORE_HA_TOKEN"
WS_PATH: Final = "/api/websocket"


class TransportConfigError(ValueError):
    """The environment does not describe a usable transport."""


class Transport(Protocol):
    """Where to connect and which token to authenticate with."""

    @property
    def name(self) -> str: ...

    @property
    def url(self) -> str: ...

    @property
    def token(self) -> str: ...


@dataclass(frozen=True, slots=True)
class SupervisorTransport:
    """Inside a Home Assistant app: the Core WebSocket proxy of the Supervisor."""

    token: str = field(repr=False)
    url: str = SUPERVISOR_WS_URL
    name: str = "supervisor"

    def __post_init__(self) -> None:
        if not self.token:
            msg = f"{ENV_SUPERVISOR_TOKEN} is empty"
            raise TransportConfigError(msg)


def derive_ws_url(base_url: str) -> str:
    """Derive the WebSocket endpoint from a Home Assistant base URL."""
    parts = urlsplit(base_url.strip())
    scheme = {"http": "ws", "https": "wss", "ws": "ws", "wss": "wss"}.get(parts.scheme)
    if scheme is None or not parts.netloc:
        msg = f"{ENV_HA_URL} must be an http(s):// or ws(s):// URL with a host"
        raise TransportConfigError(msg)
    if parts.username or parts.password:
        msg = f"{ENV_HA_URL} must not contain credentials"
        raise TransportConfigError(msg)
    path = parts.path.rstrip("/")
    if not path.endswith(WS_PATH):
        path += WS_PATH
    return urlunsplit((scheme, parts.netloc, path, "", ""))


@dataclass(frozen=True, slots=True)
class DirectTransport:
    """Direct connection with a long-lived access token."""

    token: str = field(repr=False)
    url: str
    name: str = "direct"

    def __post_init__(self) -> None:
        if not self.token:
            msg = f"{ENV_HA_TOKEN} is empty"
            raise TransportConfigError(msg)

    @classmethod
    def from_base_url(cls, base_url: str, token: str) -> DirectTransport:
        return cls(token=token, url=derive_ws_url(base_url))


def transport_from_env(env: Mapping[str, str]) -> Transport:
    """Choose the transport from environment variables.

    ``SUNCORE_HA_URL`` (with ``SUNCORE_HA_TOKEN``) selects :class:`DirectTransport`; it
    wins over ``SUPERVISOR_TOKEN`` so a developer can point an app container at another
    instance explicitly. Otherwise ``SUPERVISOR_TOKEN`` selects
    :class:`SupervisorTransport`. Neither -> :class:`TransportConfigError`.
    """
    url = env.get(ENV_HA_URL, "").strip()
    if url:
        token = env.get(ENV_HA_TOKEN, "").strip()
        if not token:
            msg = f"{ENV_HA_URL} is set but {ENV_HA_TOKEN} is missing"
            raise TransportConfigError(msg)
        return DirectTransport.from_base_url(url, token)
    supervisor_token = env.get(ENV_SUPERVISOR_TOKEN, "").strip()
    if supervisor_token:
        return SupervisorTransport(token=supervisor_token)
    msg = f"no Home Assistant access: set {ENV_SUPERVISOR_TOKEN} or {ENV_HA_URL}+{ENV_HA_TOKEN}"
    raise TransportConfigError(msg)
