# SPDX-License-Identifier: Apache-2.0
"""Packaging invariants of the two Home Assistant apps (WP9)."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
APPS = ("suncore", "suncore_beta")
# Keys that may differ between the stable and the beta app (ADR-0009).
CONFIG_DIFFERS = {"name", "slug", "image", "stage", "version", "panel_title", "description"}
LANGS = ("en", "de")


def _config(app: str) -> dict[str, Any]:
    return yaml.safe_load((ROOT / "addon" / app / "config.yaml").read_text())  # type: ignore[no-any-return]


def test_apps_are_in_sync_except_known_keys() -> None:
    stable, beta = _config("suncore"), _config("suncore_beta")
    keys = (stable.keys() | beta.keys()) - CONFIG_DIFFERS
    for key in sorted(keys):
        assert stable.get(key) == beta.get(key), f"config.yaml key {key!r} differs between apps"
    assert stable["image"] != beta["image"]
    assert stable["slug"] != beta["slug"]


@pytest.mark.parametrize("app", APPS)
def test_options_schema_and_translations_agree(app: str) -> None:
    config = _config(app)
    options, schema = config["options"], config["schema"]
    assert options.keys() == schema.keys()
    assert "demo_mode" in options
    assert options["demo_mode"] is False
    assert schema["demo_mode"] == "bool"
    for lang in LANGS:
        path = ROOT / "addon" / app / "translations" / f"{lang}.yaml"
        translated = yaml.safe_load(path.read_text())["configuration"]
        assert translated.keys() == options.keys(), f"{path}: option keys differ"
        for name, entry in translated.items():
            assert entry.get("name"), f"{path}: {name} has no name"
            assert entry.get("description"), f"{path}: {name} has no description"


def test_translations_identical_between_apps() -> None:
    for lang in LANGS:
        texts = [
            (ROOT / "addon" / app / "translations" / f"{lang}.yaml").read_text() for app in APPS
        ]
        assert texts[0] == texts[1]


def test_dockerfiles_differ_only_in_known_lines() -> None:
    stable, beta = ((ROOT / "addon" / app / "Dockerfile").read_text().splitlines() for app in APPS)
    assert len(stable) == len(beta)
    diffs = [(a, b) for a, b in zip(stable, beta, strict=True) if a != b]
    assert diffs == [
        ("COPY addon/suncore/rootfs /", "COPY addon/suncore_beta/rootfs /"),
        ('    io.hass.name="SunCore" \\', '    io.hass.name="SunCore (beta)" \\'),
    ]


@pytest.mark.parametrize("app", APPS)
def test_dockerfile_contract(app: str) -> None:
    text = (ROOT / "addon" / app / "Dockerfile").read_text()
    # Node stage pinned by digest, Python stage copies the build output.
    assert re.search(r"^FROM .*node:22-alpine@sha256:[0-9a-f]{64} AS frontend$", text, re.M)
    assert "COPY --from=frontend /src/frontend/dist /opt/suncore/frontend/dist" in text
    assert "SUNCORE_STATIC_DIR=/opt/suncore/frontend/dist" in text
    assert "frontend/.npmrc" in text
    # Locked dependencies from PyPI only, wheels only.
    assert "--only-binary=:all:" in text
    assert "--require-hashes" in text
    assert "-u UV_EXTRA_INDEX_URL" in text
    # s6 service instead of a CMD; health check against /health.
    assert not re.search(r"^CMD\b", text, re.M)
    assert "--version" not in text
    assert "http://127.0.0.1:8099/health" in text
    assert re.search(r"^HEALTHCHECK\b", text, re.M)
    assert f"COPY addon/{app}/rootfs /" in text


@pytest.mark.parametrize("app", APPS)
def test_from_args_are_global(app: str) -> None:
    """An ARG used in a FROM line must be declared before the first FROM (Docker scoping)."""
    lines = (ROOT / "addon" / app / "Dockerfile").read_text().splitlines()
    first_from = next(i for i, line in enumerate(lines) if line.startswith("FROM "))
    global_args = {
        line.split()[1].split("=")[0] for line in lines[:first_from] if line.startswith("ARG ")
    }
    for line in lines:
        if line.startswith("FROM "):
            for name in re.findall(r"\$\{?([A-Z_]+)\}?", line):
                if name in {"BUILDPLATFORM", "TARGETPLATFORM"}:
                    continue  # automatic platform args
                assert name in global_args, f"{name} in {line!r} is not a global ARG"


@pytest.mark.parametrize("app", APPS)
def test_s6_service_scripts(app: str) -> None:
    service = ROOT / "addon" / app / "rootfs" / "etc" / "services.d" / "suncore"
    run = (service / "run").read_text()
    assert run.startswith("#!/usr/bin/with-contenv bashio\n")
    assert "exec python3 -m suncore" in run
    assert (service / "finish").read_text().startswith("#!/usr/bin/env bashio\n")
    # Executable bit as git stores it (a lost bit makes s6 skip the service).
    listing = subprocess.run(
        ["git", "ls-files", "-s", "--", str(service)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    if not listing:
        pytest.skip("not in a git checkout")
    modes = {line.split()[3].rsplit("/", 1)[-1]: line.split()[0] for line in listing.splitlines()}
    assert modes == {"run": "100755", "finish": "100755"}


def test_smoke_script_is_executable_and_in_workflow() -> None:
    listing = subprocess.run(
        ["git", "ls-files", "-s", "--", "scripts/smoke_image.sh"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    if listing:
        assert listing.split()[0] == "100755"
    workflow = (ROOT / ".github" / "workflows" / "build.yml").read_text()
    assert "scripts/smoke_image.sh" in workflow
