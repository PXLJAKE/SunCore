# SPDX-License-Identifier: Apache-2.0
"""Boundary rules of the HA entity surface (plan Phase 6 section 4, ADR-0016 decision 1).

* ``suncore.ha.mqtt`` never writes to a device: no ``apply``/``release``/``call_service``/
  ``call_action_response`` and no import of the safety layer, the controller, the devices or
  the API.
* The only Supervisor REST path SunCore uses is ``GET /services/mqtt``, so ``hassio_api`` stays
  off (verified: ``/services*`` is reachable without it).
* Both app folders keep ``hassio_api`` off and ask for the MQTT service.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
PACKAGE = ROOT / "suncore"
MQTT = PACKAGE / "ha" / "mqtt"

FORBIDDEN_CALLS = frozenset(
    {
        "apply",
        "release",
        "arm_fallback",
        "keep_alive",
        "call_service",
        "call_action_response",
        "service_caller",
        "emergency_stop",
        "clear_emergency_stop",
        "override_device",
        "set_global_mode",
        "set_device_mode",
        "set_battery_mode",
        "confirm_proposal",
        "put_section",
        "submit",
        "hold",
        "dismiss_proposal",
        "set_section",
        "restore_section",
    }
)
FORBIDDEN_IMPORTS = (
    "suncore.safety",
    "suncore.controller",
    "suncore.devices",
    "suncore.api",
    "suncore.app",
    "suncore.planner",
    "suncore.storage",
)
SUPERVISOR_URL = re.compile(r"^https?://supervisor")
ALLOWED_SUPERVISOR_URL = "http://supervisor/services/mqtt"


def _files(directory: Path) -> list[Path]:
    return sorted(directory.glob("*.py"))


@pytest.mark.parametrize("path", _files(MQTT), ids=lambda p: p.name)
def test_mqtt_package_never_calls_a_write_path(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_CALLS:
            pytest.fail(f"{path.name}:{node.lineno} references .{node.attr}")
        if isinstance(node, ast.Name) and node.id in FORBIDDEN_CALLS:
            pytest.fail(f"{path.name}:{node.lineno} references {node.id}")


@pytest.mark.parametrize("path", _files(MQTT), ids=lambda p: p.name)
def test_mqtt_package_imports_only_core_i18n_and_the_ha_package(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names = [node.module]
        for name in names:
            assert not name.startswith(FORBIDDEN_IMPORTS), f"{path.name} imports {name}"


def test_commands_reach_the_system_only_through_the_command_port() -> None:
    """Every call on the injected port is a ``command_*`` or ``stored_*`` method (PR 6-D)."""
    tree = ast.parse((MQTT / "commands.py").read_text(encoding="utf-8"))
    used: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "port"
        ) or (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == "_port"
        ):
            used.add(node.attr)
    assert used
    assert all(name.startswith(("command_", "stored_")) for name in used), used


def test_the_app_command_port_calls_only_service_methods() -> None:
    """``RuntimeCommandPort`` forwards to the application services (what the web UI calls) and
    never reaches the gate, the stack, a driver or the write function."""
    path = PACKAGE / "app" / "ha_entities.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    port = next(
        n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == "RuntimeCommandPort"
    )
    banned = {"gate", "stack", "apply", "release", "submit", "hold", "resume", "put_section"}
    for node in ast.walk(port):
        if isinstance(node, ast.Attribute):
            assert node.attr not in banned, f"RuntimeCommandPort uses .{node.attr}"


def test_the_only_supervisor_rest_url_is_the_mqtt_service() -> None:
    found: list[tuple[str, str]] = []
    for path in sorted(PACKAGE.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and SUPERVISOR_URL.match(node.value)
            ):
                found.append((str(path.relative_to(ROOT)), node.value))
    assert found == [("suncore/ha/mqtt/broker.py", ALLOWED_SUPERVISOR_URL)]


@pytest.mark.parametrize("slug", ["suncore", "suncore_beta"])
def test_app_configs_ask_for_mqtt_but_not_for_the_supervisor_api(slug: str) -> None:
    config = yaml.safe_load((ROOT / "addon" / slug / "config.yaml").read_text(encoding="utf-8"))
    assert "mqtt:want" in config["services"]
    assert "hassio_api" not in config
    assert config["homeassistant_api"] is True


def test_both_app_configs_agree_on_the_service_keys() -> None:
    def load(slug: str) -> dict[str, object]:
        text = (ROOT / "addon" / slug / "config.yaml").read_text(encoding="utf-8")
        return yaml.safe_load(text)

    stable, beta = load("suncore"), load("suncore_beta")
    for key in ("services", "homeassistant_api", "ingress", "arch", "map"):
        assert stable.get(key) == beta.get(key), key
