# SPDX-License-Identifier: Apache-2.0
"""scripts/beta_version.py: beta app version derivation (ADR-0009 addendum)."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "beta_version.py"
DOCKER_TAG = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")


@pytest.fixture(scope="module")
def mod() -> ModuleType:
    spec = importlib.util.spec_from_file_location("beta_version", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_same_base_uses_run_number(mod: ModuleType) -> None:
    assert mod.next_beta_version("0.2.0", "0.3.0-beta.1", 57) == "0.3.0-beta.57"


def test_counter_never_repeats_or_goes_back(mod: ModuleType) -> None:
    assert mod.next_beta_version("0.2.0", "0.3.0-beta.90", 12) == "0.3.0-beta.91"


def test_release_catches_up_moves_base_to_next_minor(mod: ModuleType) -> None:
    assert mod.next_beta_version("0.2.0", "0.2.0-beta.1", 40) == "0.3.0-beta.40"
    assert mod.next_beta_version("0.3.0", "0.3.0-beta.55", 60) == "0.4.0-beta.60"


def test_base_is_always_stable_minor_plus_one(mod: ModuleType) -> None:
    assert mod.next_beta_version("0.2.5", "0.3.0-beta.1", 8) == "0.3.0-beta.8"
    assert mod.next_beta_version("1.4.0", "0.9.0-beta.30", 8) == "1.5.0-beta.8"


def test_hand_edited_higher_base_is_refused(mod: ModuleType) -> None:
    with pytest.raises(ValueError, match="backwards"):
        mod.next_beta_version("0.2.0", "1.0.0-beta.1", 8)


@pytest.mark.parametrize("bad", ["0.2", "0.2.0-beta", "v0.2.0", "0.2.0-rc.1"])
def test_rejects_malformed_beta(mod: ModuleType, bad: str) -> None:
    with pytest.raises(ValueError, match="beta version"):
        mod.next_beta_version("0.2.0", bad, 1)


def test_rejects_prerelease_stable_and_zero_run(mod: ModuleType) -> None:
    with pytest.raises(ValueError, match="stable version"):
        mod.next_beta_version("0.2.0-beta.1", "0.3.0-beta.1", 1)
    with pytest.raises(ValueError, match="run number"):
        mod.next_beta_version("0.2.0", "0.3.0-beta.1", 0)


def test_result_is_a_docker_tag_and_parses(mod: ModuleType) -> None:
    version = mod.next_beta_version("0.2.0", "0.3.0-beta.1", 123456)
    assert DOCKER_TAG.match(version)
    assert "+" not in version
    assert mod.parse_beta(version) == ((0, 3, 0), 123456)


def test_is_newer_orders_numerically(mod: ModuleType) -> None:
    assert mod.is_newer("0.3.0-beta.10", "0.3.0-beta.9")
    assert mod.is_newer("0.4.0-beta.1", "0.3.0-beta.99")
    assert not mod.is_newer("0.3.0-beta.9", "0.3.0-beta.9")


def test_write_preserves_file_and_is_idempotent(mod: ModuleType, tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    config.write_text('# c\nname: x\nversion: "0.2.0-beta.1" # note\nslug: s\n', encoding="utf-8")
    assert mod.write_version(config, "0.3.0-beta.7") is True
    assert config.read_text(encoding="utf-8") == (
        '# c\nname: x\nversion: "0.3.0-beta.7" # note\nslug: s\n'
    )
    assert mod.write_version(config, "0.3.0-beta.7") is False


def test_cli_compute_and_write(
    mod: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stable = tmp_path / "stable.yaml"
    beta = tmp_path / "beta.yaml"
    stable.write_text('version: "0.2.0" # x-release-please-version\n', encoding="utf-8")
    beta.write_text('version: "0.2.0-beta.1"\n', encoding="utf-8")
    assert (
        mod.main(["compute", "--run-number", "5", "--stable", str(stable), "--beta", str(beta)])
        == 0
    )
    version = capsys.readouterr().out.strip()
    assert version == "0.3.0-beta.5"
    assert mod.main(["write", version, "--beta", str(beta)]) == 0
    assert mod.read_version(beta) == version
    assert mod.main(["write", "0.3.0-beta.4", "--beta", str(beta)]) == 3
    assert mod.main(["write", version, "--beta", str(beta)]) == 0


def test_repository_configs_are_valid(mod: ModuleType) -> None:
    stable = mod.read_version(mod.STABLE_CONFIG)
    beta = mod.read_version(mod.BETA_CONFIG)
    mod.parse_stable(stable)
    mod.parse_beta(beta)
    assert mod.next_beta_version(stable, beta, 1)
