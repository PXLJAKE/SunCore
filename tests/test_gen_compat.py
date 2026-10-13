# SPDX-License-Identifier: Apache-2.0
"""scripts/gen_compat.py: the committed compatibility list matches the templates."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "gen_compat.py"


@pytest.fixture(scope="module")
def mod() -> ModuleType:
    spec = importlib.util.spec_from_file_location("gen_compat", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_committed_list_is_up_to_date(mod: ModuleType) -> None:
    assert mod.DEFAULT_OUTPUT.read_text(encoding="utf-8") == mod.render()


def test_render_is_deterministic_and_complete(mod: ModuleType) -> None:
    first = mod.render()
    assert first == mod.render()
    for template_id in ("lg.thinq_climate", "generic.switch", "tibber.price", "solcast.pv", "de"):
        assert f"| `{template_id}` |" in first
    assert "LG ThinQ air conditioner" in first
    assert mod.NOT_TESTED in first


def test_check_mode(mod: ModuleType, tmp_path: Path) -> None:
    out = tmp_path / "compat.md"
    assert mod.main(["--check", str(out)]) == 1  # missing
    assert mod.main([str(out)]) == 0
    assert mod.main(["--check", str(out)]) == 0
    out.write_text(out.read_text(encoding="utf-8") + "x\n", encoding="utf-8")
    assert mod.main(["--check", str(out)]) == 1  # drifted
