# SPDX-License-Identifier: Apache-2.0
"""Settings: options.json, environment overrides, defaults."""

from __future__ import annotations

import json
from datetime import time
from pathlib import Path

import pytest

from suncore.app.settings import (
    SettingsError,
    load_settings,
    under_supervisor,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    import os  # noqa: PLC0415

    for key in list(os.environ):
        if key.startswith("SUNCORE_"):
            monkeypatch.delenv(key)


def test_defaults_without_options_file(tmp_path: Path) -> None:
    s = load_settings(tmp_path / "missing.json")
    assert s.log_level == "info"
    assert s.controller_interval_s == 5
    assert s.demo_mode is False
    assert s.port == 8099
    assert s.data_dir == Path("/data")
    assert s.allow_any_peer is False
    assert s.max_import_bytes == 1024 * 1024
    assert s.demo_speed > 0


def test_reads_options_json(tmp_path: Path) -> None:
    path = tmp_path / "options.json"
    path.write_text(json.dumps({"log_level": "debug", "controller_interval_s": 10, "future": 1}))
    s = load_settings(path)
    assert s.log_level == "debug"
    assert s.controller_interval_s == 10


def test_env_overrides_options(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "options.json"
    path.write_text(json.dumps({"log_level": "debug", "controller_interval_s": 10}))
    monkeypatch.setenv("SUNCORE_LOG_LEVEL", "warning")
    monkeypatch.setenv("SUNCORE_PORT", "9000")
    monkeypatch.setenv("SUNCORE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SUNCORE_ALLOW_ANY_PEER", "1")
    monkeypatch.setenv("SUNCORE_MAX_IMPORT_BYTES", "2048")
    monkeypatch.setenv("SUNCORE_DEMO_SPEED", "120")
    s = load_settings(path)
    assert (s.log_level, s.controller_interval_s) == ("warning", 10)
    assert s.port == 9000
    assert s.data_dir == tmp_path
    assert s.allow_any_peer is True
    assert s.max_import_bytes == 2048
    assert s.demo_speed == 120


@pytest.mark.parametrize("var", ["SUNCORE_DEMO", "SUNCORE_DEMO_MODE"])
def test_demo_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, var: str) -> None:
    monkeypatch.setenv(var, "1")
    assert load_settings(tmp_path / "x.json").demo_mode is True


def test_options_path_from_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "o.json"
    path.write_text('{"log_level": "error"}')
    monkeypatch.setenv("SUNCORE_OPTIONS_PATH", str(path))
    assert load_settings().log_level == "error"


def test_kwargs_win(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUNCORE_PORT", "9000")
    assert load_settings(tmp_path / "x.json", port=1234).port == 1234


def test_invalid_json_raises(tmp_path: Path) -> None:
    path = tmp_path / "options.json"
    path.write_text("{nope")
    with pytest.raises(SettingsError, match="not valid JSON"):
        load_settings(path)


def test_non_object_raises(tmp_path: Path) -> None:
    path = tmp_path / "options.json"
    path.write_text("[1]")
    with pytest.raises(SettingsError, match="JSON object"):
        load_settings(path)


def test_out_of_range_interval_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "options.json"
    path.write_text('{"controller_interval_s": 1}')
    with pytest.raises(ValueError, match="controller_interval_s"):
        load_settings(path)


def test_under_supervisor() -> None:
    assert under_supervisor({"SUPERVISOR_TOKEN": "abc"}) is True
    assert under_supervisor({"SUPERVISOR_TOKEN": "  "}) is False
    assert under_supervisor({}) is False


def test_demo_start_time(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert load_settings(tmp_path / "x.json").demo_start_time == time(10, 0)
    monkeypatch.setenv("SUNCORE_DEMO_START_TIME", "13:30")
    assert load_settings(tmp_path / "x.json").demo_start_time == time(13, 30)
