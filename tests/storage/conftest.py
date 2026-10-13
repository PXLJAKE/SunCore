# SPDX-License-Identifier: Apache-2.0
"""Shared storage fixtures."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from suncore.core.site import BatteryConfig, EntityRef, GridConfig, PvArray, SiteConfig
from suncore.storage import Database


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[Database]:
    database = await Database.open(tmp_path / "suncore.db")
    yield database
    await database.close()


def make_site(grid: str = "sensor.grid_power") -> SiteConfig:
    return SiteConfig(
        grid=GridConfig(power=EntityRef(entity_id=grid)),
        pv=[PvArray(name="PV", power=EntityRef(entity_id="sensor.pv_power"))],
        battery=BatteryConfig(
            power=EntityRef(entity_id="sensor.bat_power"),
            soc=EntityRef(entity_id="sensor.bat_soc"),
        ),
    )
