# SPDX-License-Identifier: Apache-2.0
"""The verification procedure is not a writer (plan phase 7 WP8 §4, PR 7-J; AST scan).

* ``suncore.app.verification`` never calls an actuator (``apply``, ``release``, ``keep_alive``,
  ``arm_fallback``) and imports no driver module: every write is ``gate.submit`` or
  ``gate.release_device``;
* only ``suncore.app.verification`` opens a verification window, and only
  ``suncore.app.verification`` builds intent ids of a window (``verification_intent_id``) -
  no other path can reach the X3 waiver;
* nothing persists a window: no module under ``suncore/storage`` names it.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "suncore"
PROCEDURE = PACKAGE / "app" / "verification.py"
FORBIDDEN_CALLS = frozenset({"apply", "release", "keep_alive", "arm_fallback"})


def _calls(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute):
                names.add(func.attr)
            elif isinstance(func, ast.Name):
                names.add(func.id)
    return names


def _module(path: Path) -> str:
    return ".".join(path.relative_to(ROOT).with_suffix("").parts)


def test_the_procedure_never_calls_an_actuator() -> None:
    tree = ast.parse(PROCEDURE.read_text())
    assert not _calls(tree) & FORBIDDEN_CALLS
    imports = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    assert not any(m.startswith(("suncore.devices", "suncore.ha")) for m in imports), imports
    assert "suncore.safety.actuator" not in imports


def test_the_procedure_writes_only_through_the_gate() -> None:
    calls = _calls(ast.parse(PROCEDURE.read_text()))
    assert {"submit", "release_device", "open_verification_window"} <= calls


def test_only_the_procedure_opens_a_window_or_makes_window_intents() -> None:
    users: dict[str, set[str]] = {
        "open_verification_window": set(),
        "verification_intent_id": set(),
    }
    for path in PACKAGE.rglob("*.py"):
        calls = _calls(ast.parse(path.read_text()))
        for name, found in users.items():
            if name in calls:
                found.add(_module(path))
    assert users["open_verification_window"] == {"suncore.app.verification"}
    assert users["verification_intent_id"] == {"suncore.app.verification"}


def test_no_storage_module_knows_the_window() -> None:
    for path in (PACKAGE / "storage").rglob("*.py"):
        text = path.read_text()
        assert "VerificationWindow" not in text, path
        assert "verification_window" not in text, path
