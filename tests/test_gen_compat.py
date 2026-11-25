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


@pytest.mark.parametrize("lang", ["en", "de"])
def test_committed_list_is_up_to_date(mod: ModuleType, lang: str) -> None:
    assert mod.OUTPUTS[lang].read_text(encoding="utf-8") == mod.render(lang)


@pytest.mark.parametrize("lang", ["en", "de"])
def test_render_is_deterministic_and_complete(mod: ModuleType, lang: str) -> None:
    first = mod.render(lang)
    assert first == mod.render(lang)
    for template_id in ("lg.thinq_climate", "generic.switch", "tibber.price", "solcast.pv", "de"):
        assert f"| `{template_id}` |" in first
    assert ("LG ThinQ air conditioner" if lang == "en" else "LG-ThinQ-Klimagerät") in first
    assert mod.TEXT[lang]["not_tested"] in first


def test_languages_differ_in_prose_not_in_rows(mod: ModuleType) -> None:
    assert "# Compatibility list" in mod.render("en")
    assert "# Kompatibilitätsliste" in mod.render("de")


def test_check_mode(mod: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    outputs = {"en": tmp_path / "en" / "compat.md", "de": tmp_path / "de" / "compat.md"}
    monkeypatch.setattr(mod, "OUTPUTS", outputs)
    assert mod.main(["--check"]) == 1  # missing
    assert mod.main([]) == 0
    assert mod.main(["--check"]) == 0
    outputs["de"].write_text(outputs["de"].read_text(encoding="utf-8") + "x\n", encoding="utf-8")
    assert mod.main(["--check"]) == 1  # drifted
