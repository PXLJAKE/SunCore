# SPDX-License-Identifier: Apache-2.0
"""scripts/gen_params.py: the committed parameter reference matches the registry."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from suncore.core.params import REGISTRY

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "gen_params.py"


@pytest.fixture(scope="module")
def mod() -> ModuleType:
    spec = importlib.util.spec_from_file_location("gen_params", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("lang", ["en", "de"])
def test_committed_reference_is_up_to_date(mod: ModuleType, lang: str) -> None:
    assert mod.OUTPUTS[lang].read_text(encoding="utf-8") == mod.render(lang)


@pytest.mark.parametrize("lang", ["en", "de"])
def test_every_parameter_is_listed_once_with_its_help(mod: ModuleType, lang: str) -> None:
    text = mod.render(lang)
    assert text == mod.render(lang)  # deterministic
    for key in REGISTRY:
        assert text.count(f"| `{key}` |") == 1, key
    assert "Safety" in text or "Sicherheit" in text
    assert "<!-- Generated" in text or "<!-- Generiert" in text


def test_check_mode(mod: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    outputs = {"en": tmp_path / "parameters.md", "de": tmp_path / "parameters.de.md"}
    monkeypatch.setattr(mod, "OUTPUTS", outputs)
    assert mod.main(["--check"]) == 1  # missing
    assert mod.main([]) == 0
    assert mod.main(["--check"]) == 0
    outputs["de"].write_text(outputs["de"].read_text(encoding="utf-8") + "x\n", encoding="utf-8")
    assert mod.main(["--check"]) == 1  # drifted
