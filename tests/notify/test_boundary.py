# SPDX-License-Identifier: Apache-2.0
"""Boundary rules of the notification layer (plan Phase 6 section 4, ADR-0016 decision 1).

* ``suncore.notify`` never writes to a device, never changes a mode or the emergency stop and
  never reaches the gate: only the injected ``ProposalPort`` may confirm a proposal.
* ``HAConnection.notify`` is used only by ``suncore.notify`` (and implemented in ``ha``/``sim``).
* The confirm path calls exactly one service method, and no mode-changing one.
* Every notification text key of the code exists, with its title, in both catalogues.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "suncore"
NOTIFY = PACKAGE / "notify"
LOCALES = ROOT / "frontend" / "src" / "lib" / "i18n" / "locales"

FORBIDDEN = frozenset(
    {
        "apply",
        "release",
        "arm_fallback",
        "keep_alive",
        "call_service",
        "call_action_response",
        "service_caller",
        "clear_emergency_stop",
        "override_device",
        "set_global_mode",
        "set_device_mode",
        "set_battery_mode",
        "dismiss_proposal",
        "put_section",
        "submit_intent",
        "hold",
        "set_section",
        "restore_section",
        "gate",
        "stack",
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
    "suncore.ha.websocket",
    "suncore.ha.mqtt",
)


def _files(directory: Path) -> list[Path]:
    return sorted(directory.glob("*.py"))


@pytest.mark.parametrize("path", _files(NOTIFY), ids=lambda p: p.name)
def test_notify_package_never_calls_a_write_path(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):  # ``emergency_stop`` is also an observed flag: no calls
            func = node.func
            called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            assert called != "emergency_stop", f"{path.name}:{node.lineno} calls emergency_stop"
        if isinstance(node, ast.Attribute) and node.attr in FORBIDDEN:
            pytest.fail(f"{path.name}:{node.lineno} references .{node.attr}")
        if isinstance(node, ast.Name) and node.id in FORBIDDEN:
            pytest.fail(f"{path.name}:{node.lineno} references {node.id}")


@pytest.mark.parametrize("path", _files(NOTIFY), ids=lambda p: p.name)
def test_notify_package_imports_core_i18n_and_the_connection_types_only(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names = [node.module]
        for name in names:
            assert not name.startswith(FORBIDDEN_IMPORTS), f"{path.name} imports {name}"


def test_the_confirm_call_exists_only_in_the_service_and_goes_through_the_port() -> None:
    sites: list[tuple[str, str]] = []
    for path in _files(NOTIFY):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Attribute) and node.attr == "confirm_proposal":
                owner = ast.unparse(node.value)
                sites.append((path.name, owner))
    assert sites == [("service.py", "self._proposals")]


def test_the_proposal_port_offers_nothing_but_listing_and_confirming() -> None:
    tree = ast.parse((NOTIFY / "service.py").read_text(encoding="utf-8"))
    port = next(
        n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == "ProposalPort"
    )
    methods = {n.name for n in port.body if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)}
    assert methods == {"open_proposals", "confirm_proposal"}


def test_the_app_port_calls_only_the_two_service_methods() -> None:
    tree = ast.parse((PACKAGE / "app" / "notifications.py").read_text(encoding="utf-8"))
    port = next(
        n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == "RuntimeProposalPort"
    )
    used = {
        n.attr
        for n in ast.walk(port)
        if isinstance(n, ast.Attribute)
        and isinstance(n.value, ast.Attribute)
        and n.value.attr == "_rt"
        and isinstance(n.value.value, ast.Name)
    }
    assert used <= {"proposals", "confirm_proposal", "devices"}, used
    assert {"proposals", "confirm_proposal"} <= used
    banned = FORBIDDEN | {"submit", "resume", "set_global_mode", "set_device_mode"}
    for node in ast.walk(port):
        if isinstance(node, ast.Attribute):
            assert node.attr not in banned, f"RuntimeProposalPort uses .{node.attr}"


def test_ha_connection_notify_is_used_only_by_the_notify_package() -> None:
    owners = (PACKAGE / "ha", PACKAGE / "sim", NOTIFY)
    offenders: list[str] = []
    for path in sorted(PACKAGE.rglob("*.py")):
        if any(owner in path.parents for owner in owners):
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            hit = (
                (isinstance(node, ast.Attribute) and node.attr == "notify")
                or (isinstance(node, ast.Name) and node.id == "notify")
                or (
                    isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
                    and node.name == "notify"
                )
            )
            if hit:
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert not offenders, offenders


def test_the_notify_call_site_is_the_sink_and_nothing_sends_a_device_service() -> None:
    sink = ast.parse((NOTIFY / "sink.py").read_text(encoding="utf-8"))
    domains = {
        n.args[0].value
        for n in ast.walk(sink)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "_call"
        and n.args
        and isinstance(n.args[0], ast.Constant)
    }
    assert domains <= {"notify", "persistent_notification"}


# --- catalogue ---

_KEY = re.compile(r"^notify\.[a-z0-9_.]+$")


def _log_events(tree: ast.AST) -> set[str]:
    """First arguments of ``log.<level>(...)`` calls: structured-log event names, not texts."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "log"
            and node.args
            and isinstance(node.args[0], ast.Constant)
        ):
            names.add(str(node.args[0].value))
    return names


def _notify_keys() -> set[str]:
    keys: set[str] = set()
    for path in [*NOTIFY.glob("*.py"), PACKAGE / "app" / "notifications.py"]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        logs = _log_events(tree)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and _KEY.match(node.value)
                and not node.value.endswith(".")
                and node.value not in logs
            ):
                keys.add(node.value)
    runtime = ast.parse((PACKAGE / "app" / "runtime.py").read_text(encoding="utf-8"))
    for node in ast.walk(runtime):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value.startswith("notify.")
        ):
            keys.add(node.value)
    return keys


@pytest.mark.parametrize("language", ["en", "de"])
def test_every_notification_text_key_has_a_message_and_a_title(language: str) -> None:
    catalogue = json.loads((LOCALES / f"{language}.json").read_text(encoding="utf-8"))
    keys = _notify_keys()
    assert len(keys) >= 15
    missing: list[str] = []
    for key in sorted(keys):
        if key.startswith(("notify.reason.", "notify.action.")) or key == "notify.panel_hint":
            if key not in catalogue:
                missing.append(key)
            continue
        for needed in (key, f"{key}.title"):
            if needed not in catalogue:
                missing.append(needed)
    assert not missing, missing


def test_the_proposal_result_keys_built_from_the_outcome_exist() -> None:
    catalogue = json.loads((LOCALES / "en.json").read_text(encoding="utf-8"))
    for outcome in ("confirmed", "rejected", "gone", "refused"):
        assert f"notify.proposal.{outcome}" in catalogue
        assert f"notify.proposal.{outcome}.title" in catalogue
    for flag in ("island", "para14a"):
        for edge in ("start", "end"):
            assert f"notify.safety.{flag}_{edge}" in catalogue
