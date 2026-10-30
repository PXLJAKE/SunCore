# SPDX-License-Identifier: Apache-2.0
"""Generated schemas: drift guard, shape, and the validate_templates lint."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

from tests.templates.conftest import device_doc, write_template

ROOT = Path(__file__).resolve().parents[2]


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def gen() -> ModuleType:
    return _load("gen_schemas")


@pytest.fixture(scope="module")
def lint() -> ModuleType:
    return _load("validate_templates")


def test_committed_schemas_are_up_to_date(gen: ModuleType) -> None:
    assert gen.main(["--check"]) == 0


def test_check_detects_drift_missing_and_stale(gen: ModuleType, tmp_path: Path) -> None:
    assert gen.main([str(tmp_path)]) == 0
    assert gen.main(["--check", str(tmp_path)]) == 0
    (tmp_path / "device.schema.json").write_text("{}\n", encoding="utf-8")
    assert gen.main(["--check", str(tmp_path)]) == 1
    gen.main([str(tmp_path)])
    (tmp_path / "region.schema.json").unlink()
    assert gen.main(["--check", str(tmp_path)]) == 1
    gen.main([str(tmp_path)])
    (tmp_path / "old.schema.json").write_text("{}\n", encoding="utf-8")
    assert gen.main(["--check", str(tmp_path)]) == 1


def test_schema_shape(gen: ModuleType) -> None:
    files = gen.render()
    assert set(files) == {
        "device.schema.json",
        "tariff.schema.json",
        "forecast.schema.json",
        "region.schema.json",
        "battery.schema.json",
        "template.schema.json",
    }
    union = json.loads(files["template.schema.json"])
    assert union["discriminator"]["propertyName"] == "kind"
    device = json.loads(files["device.schema.json"])
    assert device["additionalProperties"] is False
    assert device["properties"]["schema_version"]["const"] == 1
    assert "source" in device["required"]


def test_lint_accepts_valid_and_reports_problems(lint: ModuleType, tmp_path: Path) -> None:
    locales = tmp_path / "locales"
    locales.mkdir()
    keys = {
        "template.acme.heater.name": "x",
        "template.acme.heater.role.climate": "x",
        "template.acme.heater.role.energy": "x",
    }
    (locales / "en.json").write_text(json.dumps(keys), encoding="utf-8")
    (locales / "de.json").write_text(json.dumps(keys), encoding="utf-8")
    root = tmp_path / "templates"
    write_template(root, device_doc())
    count, problems = lint.validate(root, locales)
    assert (count, problems) == (1, [])

    del keys["template.acme.heater.name"]
    (locales / "de.json").write_text(json.dumps(keys), encoding="utf-8")
    count, problems = lint.validate(root, locales)
    assert count == 1
    assert len(problems) == 1
    assert "template.acme.heater.name" in problems[0]
    assert "de.json" in problems[0]

    write_template(root, device_doc("acme.other"), stem="wrong")
    _, problems = lint.validate(root, locales)
    assert any("file name must be" in p for p in problems)


def test_repo_templates_pass_lint(lint: ModuleType) -> None:
    assert lint.main(["--check"]) == 0
