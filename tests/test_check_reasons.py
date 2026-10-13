# SPDX-License-Identifier: Apache-2.0
"""scripts/check_reasons.py: backend message keys exist in the frontend catalogues."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_reasons.py"
REQUIRED = {
    "safety.shadow_mode",
    "safety.emergency_stop",
    "api.error.ha_unavailable",
    "api.error.import_too_large",
    "api.error.invalid_json",
    "api.error.import_rejected",
    "api.error.not_found",
    "api.error.forbidden",
}


@pytest.fixture(scope="module")
def mod() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_reasons", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_repository_is_consistent(mod: ModuleType) -> None:
    keys = mod.collect_keys()
    assert set(keys) >= REQUIRED
    assert mod.missing_keys(keys) == {}


def test_collects_reason_keys_and_error_details(mod: ModuleType, tmp_path: Path) -> None:
    pkg = tmp_path / "pkg"
    (pkg / "api").mkdir(parents=True)
    (pkg / "x.py").write_text('R = Reason(key="a.one")\nS = Reason("a.two")\nT = "not.a.key"\n')
    (pkg / "api" / "y.py").write_text(
        'E = "api.error.boom"\nB = b\'{"detail":"api.error.json"}\'\n'
    )
    assert set(mod.collect_keys(pkg)) == {"a.one", "a.two", "api.error.boom", "api.error.json"}


def test_reports_missing_keys(mod: ModuleType, tmp_path: Path) -> None:
    (tmp_path / "en.json").write_text(json.dumps({"a.one": "x"}))
    (tmp_path / "de.json").write_text(json.dumps({}))
    gaps = mod.missing_keys({"a.one": ["f:1"]}, tmp_path)
    assert gaps == {"de": ["a.one"]}


def test_collects_wizard_and_criterion_keys_outside_api(mod: ModuleType, tmp_path: Path) -> None:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "z.py").write_text(
        'A = "wizard.check.one"\nB = "detect.criterion.name"\nC = "wizard check"\n'
        'D = "api.error.only_in_api"\nE = "wizard.has space"\nF = "wizard."\n'
    )
    assert set(mod.collect_keys(pkg)) == {"wizard.check.one", "detect.criterion.name"}


def test_collects_validation_keys_in_api(mod: ModuleType, tmp_path: Path) -> None:
    pkg = tmp_path / "pkg"
    (pkg / "api").mkdir(parents=True)
    (pkg / "api" / "v.py").write_text('K = "api.validation.missing"\n')
    assert set(mod.collect_keys(pkg)) == {"api.validation.missing"}
