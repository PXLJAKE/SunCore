# SPDX-License-Identifier: Apache-2.0
"""Smoke tests for the Phase 0 skeleton: every package imports, the CLI answers."""

from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest
import yaml

import suncore
from suncore.__main__ import build_parser, main

PACKAGES = [
    "core",
    "ha",
    "forecast",
    "tariff",
    "planner",
    "controller",
    "safety",
    "devices",
    "api",
    "storage",
    "sim",
    "app",
]

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("name", PACKAGES)
def test_package_imports(name: str) -> None:
    module = importlib.import_module(f"suncore.{name}")
    assert module.__doc__


def test_version_is_semver() -> None:
    assert re.match(r"^\d+\.\d+\.\d+", suncore.__version__)


def test_cli_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["--version"])
    assert exc.value.code == 0
    assert suncore.__version__ in capsys.readouterr().out


def test_cli_without_args_is_not_runnable_yet() -> None:
    assert main([]) == 2


@pytest.mark.parametrize("app", ["suncore", "suncore_beta"])
def test_app_config_matches_package(app: str) -> None:
    """The stable app pins the package version; both apps target the same archs."""
    config = yaml.safe_load((ROOT / "addon" / app / "config.yaml").read_text())
    assert config["slug"] == app
    assert set(config["arch"]) == {"aarch64", "amd64"}
    assert config["ingress"] is True
    # The app linter rejects keys that repeat Supervisor defaults.
    redundant = {"startup", "boot", "ingress_port", "panel_admin", "hassio_role", "backup"}
    redundant.add("watchdog")  # obsolete, HEALTHCHECK instead
    assert not redundant & config.keys()
    if app == "suncore":
        assert config["version"] == suncore.__version__


def test_every_source_file_has_spdx_header() -> None:
    for path in (ROOT / "suncore").rglob("*.py"):
        assert path.read_text().startswith("# SPDX-License-Identifier: Apache-2.0"), path
