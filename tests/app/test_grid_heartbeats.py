# SPDX-License-Identifier: Apache-2.0
"""The grid meter is the only signal that must keep reporting (``app/control.py``)."""

from __future__ import annotations

from suncore.app.control import grid_heartbeats
from tests.safety.test_watchdog import site


def test_one_signed_grid_sensor_is_one_group() -> None:
    assert grid_heartbeats(site()) == (("sensor.grid",),)


def test_an_import_export_pair_is_one_group() -> None:
    assert grid_heartbeats(site(pair=True)) == (("sensor.imp", "sensor.exp"),)


def test_no_site_means_no_heartbeat() -> None:
    assert grid_heartbeats(None) == ()
