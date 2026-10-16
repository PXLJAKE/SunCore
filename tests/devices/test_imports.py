# SPDX-License-Identifier: Apache-2.0
"""``suncore.devices`` sees only ``suncore.core`` (and itself): no HA, no safety, no storage.

The drivers get their write and read capabilities injected (protocols in
``suncore/devices/drivers/base.py``), the power models are pure.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

DEVICES = Path(__file__).resolve().parents[2] / "suncore" / "devices"
FILES = sorted(DEVICES.rglob("*.py"))


@pytest.mark.parametrize("path", FILES, ids=lambda p: str(p.relative_to(DEVICES)))
def test_devices_import_only_core(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "use absolute imports"
            imported.append(node.module or "")
    offenders = [
        m
        for m in imported
        if m.startswith("suncore.") and not m.startswith(("suncore.core", "suncore.devices"))
    ]
    assert not offenders, offenders


def test_no_file_was_missed() -> None:
    assert len(FILES) > 15
