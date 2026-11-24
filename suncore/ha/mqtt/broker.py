# SPDX-License-Identifier: Apache-2.0
"""Where the MQTT broker is: the Supervisor services API or manual settings.

``GET http://supervisor/services/mqtt`` with the app's ``SUPERVISOR_TOKEN`` is the **only**
Supervisor REST path SunCore calls (a boundary test asserts it). It needs ``services:
[mqtt:want]`` in the app config but **not** ``hassio_api``: the Supervisor's security middleware
lets apps reach ``/services*`` without it (``home-assistant/supervisor`` ``supervisor/api/
middleware/security.py`` ``api_bypass``, read 2026-11-24; ``docs/research/ha-apps.md``).

Response (``supervisor/api/services.py`` ``get_service`` and ``services/modules/mqtt.py``
``SCHEMA_SERVICE_MQTT``, read 2026-11-24): ``{"result": "ok", "data": {host, port, ssl,
username?, password?, protocol, app|addon}}``; without a provider the call fails with
``{"result": "error", "message": "Service not enabled"}``.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final, Literal, Protocol

import aiohttp
import structlog

from suncore.core.config import MqttConfig
from suncore.core.haentities import BrokerState

log = structlog.get_logger(__name__)

SUPERVISOR_MQTT_URL: Final = "http://supervisor/services/mqtt"
ENV_SUPERVISOR_TOKEN: Final = "SUPERVISOR_TOKEN"
REQUEST_TIMEOUT_S: Final = 10.0


@dataclass(frozen=True, slots=True)
class BrokerSettings:
    host: str
    port: int = 1883
    tls: bool = False
    username: str | None = None
    password: str | None = None
    protocol: Literal["3.1", "3.1.1"] = "3.1.1"

    def __repr__(self) -> str:  # never print the password
        return f"BrokerSettings(host={self.host!r}, port={self.port}, tls={self.tls})"


@dataclass(frozen=True, slots=True)
class BrokerLookup:
    state: BrokerState
    """``CONNECTING`` = settings found; otherwise the reason the surface cannot start."""
    settings: BrokerSettings | None = None
    source: Literal["supervisor", "manual"] | None = None
    reason_key: str | None = None


class ServiceNotEnabledError(Exception):
    """No app provides the MQTT service (no Mosquitto broker installed)."""


class SupervisorUnreachableError(Exception):
    """The Supervisor could not be asked or answered something unusable."""


class SupervisorServices(Protocol):
    async def mqtt_service(self) -> Mapping[str, Any]:
        """The ``data`` object of ``GET /services/mqtt``."""
        ...


class SupervisorHttpServices:
    """``GET http://supervisor/services/mqtt`` over aiohttp."""

    def __init__(
        self,
        token: str,
        session_factory: Any = aiohttp.ClientSession,
        url: str = SUPERVISOR_MQTT_URL,
    ) -> None:
        self._token = token
        self._session_factory = session_factory
        self._url = url

    async def mqtt_service(self) -> Mapping[str, Any]:
        timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_S)
        try:
            async with (
                self._session_factory(timeout=timeout) as session,
                session.get(self._url, headers={"Authorization": f"Bearer {self._token}"}) as resp,
            ):
                try:
                    body = await resp.json(content_type=None)
                except (aiohttp.ContentTypeError, ValueError) as exc:
                    raise SupervisorUnreachableError("not json") from exc
                status = resp.status
        except (aiohttp.ClientError, TimeoutError, OSError) as exc:
            raise SupervisorUnreachableError(type(exc).__name__) from exc
        return _data_of(body, status)


def _data_of(body: object, status: int) -> Mapping[str, Any]:
    if not isinstance(body, dict):
        raise SupervisorUnreachableError("unexpected body")
    if body.get("result") == "ok" and isinstance(body.get("data"), dict):
        return body["data"]  # type: ignore[no-any-return]
    message = str(body.get("message", "")).lower()
    if "not enabled" in message or "not provided" in message:
        raise ServiceNotEnabledError(message)
    raise SupervisorUnreachableError(f"HTTP {status}: {message[:80]}")


def supervisor_services_from_env(env: Mapping[str, str] | None = None) -> SupervisorServices | None:
    """The HTTP client when ``SUPERVISOR_TOKEN`` is set (running as an app), else ``None``."""
    source = os.environ if env is None else env
    token = source.get(ENV_SUPERVISOR_TOKEN, "").strip()
    return SupervisorHttpServices(token) if token else None


def parse_service(data: Mapping[str, Any]) -> BrokerSettings:
    """Turn the Supervisor's ``data`` into settings; raises ``ValueError`` when unusable."""
    host = data.get("host")
    port_raw = data.get("port")
    if not isinstance(host, str) or not host:
        raise ValueError("no host")
    try:
        port = int(port_raw)  # type: ignore[arg-type]  # int or numeric string (research note)
    except (TypeError, ValueError) as exc:
        raise ValueError("no port") from exc
    if not 0 < port < 65_536:
        raise ValueError("bad port")
    username = data.get("username")
    password = data.get("password")
    protocol: Literal["3.1", "3.1.1"] = "3.1" if data.get("protocol") == "3.1" else "3.1.1"
    return BrokerSettings(
        host=host,
        port=port,
        tls=data.get("ssl") is True,
        username=username if isinstance(username, str) and username else None,
        password=password if isinstance(password, str) and password else None,
        protocol=protocol,
    )


async def lookup_broker(  # noqa: PLR0911 - one return per outcome
    config: MqttConfig, services: SupervisorServices | None
) -> BrokerLookup:
    """Resolve the broker for ``config``; never raises (every failure has a reason key)."""
    if not config.enabled:
        return BrokerLookup(BrokerState.DISABLED, reason_key="mqtt.reason.not_enabled")
    if config.broker == "manual":
        if not config.host:
            return BrokerLookup(
                BrokerState.ERROR, source="manual", reason_key="mqtt.reason.manual_incomplete"
            )
        password = config.password.get_secret_value() if config.password is not None else None
        return BrokerLookup(
            BrokerState.CONNECTING,
            BrokerSettings(config.host, config.port, config.tls, config.username, password or None),
            "manual",
        )
    if services is None:
        return BrokerLookup(
            BrokerState.ERROR, source="supervisor", reason_key="mqtt.reason.not_supervised"
        )
    try:
        data = await services.mqtt_service()
        settings = parse_service(data)
    except ServiceNotEnabledError:
        return BrokerLookup(
            BrokerState.NO_BROKER, source="supervisor", reason_key="mqtt.reason.no_broker_service"
        )
    except (SupervisorUnreachableError, ValueError) as exc:
        log.warning("mqtt.broker_lookup_failed", error=str(exc))
        return BrokerLookup(
            BrokerState.ERROR, source="supervisor", reason_key="mqtt.reason.supervisor_unreachable"
        )
    return BrokerLookup(BrokerState.CONNECTING, settings, "supervisor")
