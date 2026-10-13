# SPDX-License-Identifier: Apache-2.0
"""The committed OpenAPI file (frontend type source) must match the code."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[2]


def _script() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "export_openapi", ROOT / "scripts/export_openapi.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_committed_openapi_is_current() -> None:
    script = _script()
    assert script.main(["--check"]) == 0, "run: uv run python scripts/export_openapi.py"


def test_export_writes_file(tmp_path: Path) -> None:
    out = tmp_path / "sub" / "openapi.json"
    assert _script().main([str(out)]) == 0
    spec = json.loads(out.read_text())
    assert spec["info"]["title"] == "SunCore API"
    assert "/health" in spec["paths"]


def test_check_detects_drift(tmp_path: Path) -> None:
    out = tmp_path / "openapi.json"
    out.write_text("{}\n")
    assert _script().main(["--check", str(out)]) == 1
