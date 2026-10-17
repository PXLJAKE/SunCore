#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Check that every backend message key exists in the frontend catalogues.

Collected from ``suncore/``:

* ``Reason(key="...")`` calls (AST, so comments and strings elsewhere do not count),
* string constants starting with ``api.error.`` (the error ``detail`` keys of the API) or
  ``api.validation.`` in ``suncore/api`` (validation issue keys),
* string constants that are exactly one key with the prefix ``wizard.`` (check and import
  reasons built from a variable), ``detect.criterion.`` or ``ha.error.`` anywhere
  in ``suncore/``, and ``controller.`` in ``suncore/controller/reasons.py`` (the
  controller's key constants).

Each key must be present in ``frontend/src/lib/i18n/locales/en.json`` and ``de.json``.
Run via ``uv run python scripts/check_reasons.py``; exit code 1 lists the gaps.
"""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "suncore"
LOCALES = ROOT / "frontend" / "src" / "lib" / "i18n" / "locales"
ERROR_PREFIXES = ("api.error.", "api.validation.")
KEY_PREFIXES = ("wizard.", "detect.criterion.", "ha.error.")
CONTROLLER_KEYS = Path("controller") / "reasons.py"
"""The controller defines its keys as constants in one module (log event names elsewhere
may share the ``controller.`` prefix)."""
KEY_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$")


def _is_reason_call(node: ast.Call) -> bool:
    func = node.func
    name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
    return name == "Reason"


def _reason_key(node: ast.Call) -> str | None:
    values = [kw.value for kw in node.keywords if kw.arg == "key"] or node.args[:1]
    for value in values:
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            return value.value
    return None


def _error_keys(node: ast.Constant) -> list[str]:
    """``api.error.*`` keys in a string/bytes constant (also inside JSON literals)."""
    raw = node.value
    text = raw.decode() if isinstance(raw, bytes) else raw
    if not isinstance(text, str):
        return []
    parts = text.split('"')
    return [p for p in parts if p.startswith(ERROR_PREFIXES) and " " not in p]


def _plain_key(node: ast.Constant, prefixes: tuple[str, ...] = KEY_PREFIXES) -> list[str]:
    """A constant that is exactly one key with one of ``prefixes``."""
    value = node.value
    if isinstance(value, str) and value.startswith(prefixes) and KEY_RE.match(value):
        return [value]
    return []


def collect_keys(source: Path = SOURCE) -> dict[str, list[str]]:
    """Map each used key to the ``file:line`` places that use it."""
    found: dict[str, list[str]] = {}
    for path in sorted(source.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        rel = path.relative_to(source.parent)
        in_api = path.is_relative_to(source / "api")
        prefixes = KEY_PREFIXES
        if path == source / CONTROLLER_KEYS:
            prefixes = (*KEY_PREFIXES, "controller.")
        for node in ast.walk(tree):
            keys: list[str] = []
            if isinstance(node, ast.Call) and _is_reason_call(node):
                key = _reason_key(node)
                keys = [key] if key else []
            elif isinstance(node, ast.Constant):
                keys = _error_keys(node) if in_api else []
                keys += _plain_key(node, prefixes)
            for key in keys:
                found.setdefault(key, []).append(f"{rel}:{node.lineno}")
    return found


def missing_keys(keys: dict[str, list[str]], locales: Path = LOCALES) -> dict[str, list[str]]:
    """Return ``{locale: [missing keys]}`` (only locales with gaps)."""
    gaps: dict[str, list[str]] = {}
    for catalogue in ("en", "de"):
        data = json.loads((locales / f"{catalogue}.json").read_text(encoding="utf-8"))
        absent = sorted(k for k in keys if k not in data)
        if absent:
            gaps[catalogue] = absent
    return gaps


def main() -> int:
    keys = collect_keys()
    gaps = missing_keys(keys)
    if not gaps:
        print(f"check_reasons: {len(keys)} backend keys present in en.json and de.json")
        return 0
    for catalogue, absent in gaps.items():
        for key in absent:
            where = ", ".join(keys[key])
            sys.stderr.write(f"missing in {catalogue}.json: {key} (used at {where})\n")
    return 1


if __name__ == "__main__":
    sys.exit(main())
