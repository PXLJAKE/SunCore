# SPDX-License-Identifier: Apache-2.0
"""scripts/sync_beta_translations.py: the beta app mirrors the stable app's translations."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "sync_beta_translations.py"


@pytest.fixture(scope="module")
def mod() -> ModuleType:
    spec = importlib.util.spec_from_file_location("sync_beta_translations", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _app(root: Path, name: str, options: list[str], texts: dict[str, str]) -> Path:
    app = root / name
    (app / "translations").mkdir(parents=True)
    body = "options:\n" + "".join(f"  {o}: 1\n" for o in options)
    body += "schema:\n" + "".join(f"  {o}: int\n" for o in options)
    (app / "config.yaml").write_text(body, encoding="utf-8")
    for lang, text in texts.items():
        (app / "translations" / f"{lang}.yaml").write_text(text, encoding="utf-8")
    return app


def test_repository_is_in_sync(mod: ModuleType) -> None:
    assert mod.main(["--check"]) == 0


def test_copies_byte_for_byte_and_removes_orphans(mod: ModuleType, tmp_path: Path) -> None:
    stable = _app(tmp_path, "s", ["a"], {"en": "x: 'one'\n", "de": 'x: "eins"\n'})
    beta = _app(tmp_path, "b", ["a"], {"en": "old\n", "fr": "orphan\n"})
    assert mod.main(["--check"], stable, beta) == 1
    assert mod.main([], stable, beta) == 0
    assert (beta / "translations" / "en.yaml").read_text() == "x: 'one'\n"
    assert (beta / "translations" / "de.yaml").read_text() == 'x: "eins"\n'
    assert not (beta / "translations" / "fr.yaml").exists()
    assert mod.main(["--check"], stable, beta) == 0


def test_different_option_sets_fail_and_copy_nothing(
    mod: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stable = _app(tmp_path, "s", ["a", "b"], {"en": "new\n"})
    beta = _app(tmp_path, "b", ["a"], {"en": "old\n"})
    assert mod.main([], stable, beta) == 1
    assert "different option sets" in capsys.readouterr().err
    assert (beta / "translations" / "en.yaml").read_text() == "old\n"
