# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import pytest

from suncore.ha.transport import (
    DirectTransport,
    SupervisorTransport,
    TransportConfigError,
    derive_ws_url,
    transport_from_env,
)


@pytest.mark.parametrize(
    ("base", "expected"),
    [
        ("http://ha.local:8123", "ws://ha.local:8123/api/websocket"),
        ("https://ha.example.org/", "wss://ha.example.org/api/websocket"),
        ("http://ha.local:8123/prefix/", "ws://ha.local:8123/prefix/api/websocket"),
        ("ws://ha.local:8123/api/websocket", "ws://ha.local:8123/api/websocket"),
        ("wss://ha.example.org", "wss://ha.example.org/api/websocket"),
        ("  http://10.0.0.5:8123?x=1#frag ", "ws://10.0.0.5:8123/api/websocket"),
    ],
)
def test_derive_ws_url(base: str, expected: str) -> None:
    assert derive_ws_url(base) == expected


@pytest.mark.parametrize("bad", ["", "ha.local:8123", "ftp://ha", "http://", "http://u:p@ha:8123"])
def test_derive_ws_url_rejects(bad: str) -> None:
    with pytest.raises(TransportConfigError):
        derive_ws_url(bad)


def test_supervisor_selected_from_env() -> None:
    t = transport_from_env({"SUPERVISOR_TOKEN": "sup-secret"})
    assert isinstance(t, SupervisorTransport)
    assert t.url == "ws://supervisor/core/websocket"
    assert t.token == "sup-secret"
    assert t.name == "supervisor"


def test_direct_selected_from_env_and_wins() -> None:
    env = {
        "SUPERVISOR_TOKEN": "sup-secret",
        "SUNCORE_HA_URL": "http://ha.local:8123",
        "SUNCORE_HA_TOKEN": "llat-secret",
    }
    t = transport_from_env(env)
    assert isinstance(t, DirectTransport)
    assert t.url == "ws://ha.local:8123/api/websocket"
    assert t.token == "llat-secret"


def test_env_errors() -> None:
    with pytest.raises(TransportConfigError, match="SUNCORE_HA_TOKEN"):
        transport_from_env({"SUNCORE_HA_URL": "http://ha:8123"})
    with pytest.raises(TransportConfigError, match="SUPERVISOR_TOKEN"):
        transport_from_env({})
    with pytest.raises(TransportConfigError):
        SupervisorTransport(token="")


def test_repr_hides_token() -> None:
    t = DirectTransport.from_base_url("http://ha:8123", "llat-secret")
    s = SupervisorTransport(token="sup-secret")
    assert "llat-secret" not in repr(t)
    assert "sup-secret" not in repr(s)
