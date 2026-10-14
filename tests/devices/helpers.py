# SPDX-License-Identifier: Apache-2.0
"""Builders shared by the device tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from suncore.core.control import DeviceState
from suncore.core.signals import Quality, Sample
from suncore.core.units import Unit

T0 = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def power_sample(
    value: float | None, ts: datetime, *, unit: Unit | None = Unit.W, quality: Quality = Quality.OK
) -> Sample:
    return Sample(value=value, unit=unit, ts=ts, quality=quality)


def energy_sample(
    value: float | None, ts: datetime, *, unit: Unit | None = Unit.WH, quality: Quality = Quality.OK
) -> Sample:
    return Sample(value=value, unit=unit, ts=ts, quality=quality)


def temp_sample(value: float, ts: datetime) -> Sample:
    return Sample(value=value, unit=None, ts=ts)


def climate_state(
    *,
    on: bool = True,
    mode: str | None = "cool",
    setpoint: float | None = 24.0,
    quality: Quality = Quality.OK,
) -> DeviceState:
    return DeviceState(on=on, mode=mode, setpoint_c=setpoint, quality=quality)
