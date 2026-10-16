# SPDX-License-Identifier: Apache-2.0
"""Control state contracts (Phase 3 WP9): override commands, mode requests, power sensor cadence."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from suncore.core.controlstate import DeviceModeRequest, OverrideCommand
from suncore.core.decisions import OperatingMode
from suncore.core.devices import PowerModel, PowerModelKind


def keys(exc: ValidationError) -> set[str]:
    return {str(e["type"]) for e in exc.errors()}


def test_override_commands() -> None:
    assert OverrideCommand(action="boost", minutes=30).minutes == 30
    assert OverrideCommand(action="pause").until is None  # until resumed
    assert OverrideCommand(action="pause", until="2026-06-21T18:00:00Z").until is not None  # type: ignore[arg-type]
    assert OverrideCommand(action="resume").action == "resume"


@pytest.mark.parametrize(
    ("body", "key"),
    [
        ({"action": "boost"}, "suncore.override_boost_minutes"),
        ({"action": "resume", "minutes": 5}, "suncore.override_minutes_only_boost"),
        ({"action": "pause", "minutes": 5}, "suncore.override_minutes_only_boost"),
        (
            {"action": "boost", "minutes": 5, "until": "2026-06-21T18:00:00Z"},
            "suncore.override_until_only_pause",
        ),
        ({"action": "boost", "minutes": 0}, "greater_than_equal"),
        ({"action": "boost", "minutes": 100_000}, "less_than_equal"),
        ({"action": "dance"}, "literal_error"),
        ({"action": "pause", "until": "2026-06-21T18:00:00"}, "timezone_aware"),
    ],
)
def test_invalid_override_commands(body: dict[str, object], key: str) -> None:
    with pytest.raises(ValidationError) as info:
        OverrideCommand.model_validate(body)
    assert key in keys(info.value)


def test_device_mode_request_needs_an_identity_to_raise() -> None:
    assert DeviceModeRequest(mode=OperatingMode.SHADOW).identity is None
    assert DeviceModeRequest(mode=OperatingMode.AUTOMATIC, identity="abc").identity == "abc"
    for mode in (OperatingMode.ASSISTED, OperatingMode.AUTOMATIC):
        with pytest.raises(ValidationError) as info:
            DeviceModeRequest(mode=mode)
        assert "suncore.mode_identity_required" in keys(info.value)


def test_power_update_cadence_belongs_to_a_power_sensor() -> None:
    ok = PowerModel(kind=PowerModelKind.MEASURED, power_role="power", power_update_s=300)
    assert ok.power_update_s == 300
    with pytest.raises(ValidationError) as info:
        PowerModel(
            kind=PowerModelKind.LEARNED, nominal_w=500, energy_role="energy", power_update_s=300
        )
    assert "suncore.power_update_without_role" in keys(info.value)
