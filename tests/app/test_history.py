# SPDX-License-Identifier: Apache-2.0
"""Combination of the site's statistics into measurement series (rebuild, Phase 4 WP3)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from suncore.app.history import assemble_site_series, rebuild_history, site_statistic_specs
from suncore.core.measurements import (
    SITE_BATTERY_W,
    SITE_GRID_W,
    SITE_HOUSE_W,
    SITE_PV_W,
    SITE_SOC_PCT,
)
from suncore.core.series import SeriesUnit, TimeRange, TimeSeries
from suncore.core.site import BatteryConfig, EntityRef, GridConfig, PvArray, SiteConfig
from suncore.storage import open_timeseries
from suncore.storage.timeseries import TimeseriesStore

T0 = datetime(2026, 10, 10, tzinfo=UTC)
Q = timedelta(minutes=15)


def ts(values: list[float | None], start: datetime = T0) -> TimeSeries:
    return TimeSeries(start=start, step=Q, values=tuple(values), unit=SeriesUnit.W)


def ref(name: str, *, invert: bool = False) -> EntityRef:
    return EntityRef(entity_id=f"sensor.{name}", invert_sign=invert)


def full_site(*, house: bool = False) -> SiteConfig:
    return SiteConfig(
        grid=GridConfig(power=ref("grid", invert=True)),
        pv=[
            PvArray(name="A", power=ref("pv_a")),
            PvArray(name="B", power=ref("pv_b")),
            PvArray(name="C"),
        ],
        battery=BatteryConfig(power=ref("bat"), soc=ref("soc")),
        house=ref("house") if house else None,
    )


def test_specs_cover_every_mapped_power_entity_and_the_soc() -> None:
    specs = site_statistic_specs(full_site(house=True))
    by_key = {s.key: s for s in specs}
    assert set(by_key) == {"grid", "pv0", "pv1", "battery", "soc", "house"}  # array C has no sensor
    assert by_key["grid"].invert is True
    assert by_key["soc"].unit is SeriesUnit.PERCENT
    assert all(s.kind == "mean" for s in specs)


def test_pair_grid_uses_two_statistics_and_subtracts() -> None:
    site = SiteConfig(
        grid=GridConfig(import_power=ref("imp"), export_power=ref("exp")),
        pv=[PvArray(name="A", power=ref("pv"))],
    )
    assert {s.key for s in site_statistic_specs(site)} == {"grid_import", "grid_export", "pv0"}
    out = assemble_site_series(
        site,
        {
            "grid_import": ts([500.0, 0.0]),
            "grid_export": ts([0.0, 300.0]),
            "pv0": ts([100.0, 900.0]),
        },
    )
    assert out[SITE_GRID_W].values == (500.0, -300.0)
    # no battery: house = pv + grid
    assert out[SITE_HOUSE_W].values == (600.0, 600.0)
    assert SITE_BATTERY_W not in out
    assert SITE_SOC_PCT not in out


def test_house_is_derived_like_the_live_balance() -> None:
    fetched = {
        "grid": ts([200.0, -100.0]),
        "pv0": ts([1000.0, 800.0]),
        "pv1": ts([500.0, 200.0]),
        "battery": ts([300.0, -50.0]),
        "soc": ts([40.0, 41.0]),
    }
    out = assemble_site_series(full_site(), fetched)
    assert out[SITE_PV_W].values == (1500.0, 1000.0)
    # pv + grid - battery
    assert out[SITE_HOUSE_W].values == (1400.0, 950.0)
    assert out[SITE_SOC_PCT].values == (40.0, 41.0)


def test_a_configured_house_sensor_wins() -> None:
    out = assemble_site_series(
        full_site(house=True),
        {
            "grid": ts([1.0]),
            "pv0": ts([1.0]),
            "pv1": ts([1.0]),
            "battery": ts([0.0]),
            "house": ts([42.0]),
        },
    )
    assert out[SITE_HOUSE_W].values == (42.0,)


def test_missing_parts_become_gaps_not_zeros() -> None:
    fetched = {
        "grid": ts([200.0, 300.0, 400.0]),
        "pv0": ts([1000.0, None, 1000.0]),
        "pv1": ts([500.0, 500.0, 500.0]),
        "battery": ts([0.0, 0.0], start=T0 + Q),  # shorter and later: only slots 1 and 2 overlap
    }
    out = assemble_site_series(full_site(), fetched)
    assert out[SITE_PV_W].values == (1500.0, None, 1500.0)
    assert out[SITE_HOUSE_W].values == (None, None, 1900.0)
    # a missing array sensor makes the whole PV sum unknown
    partial = assemble_site_series(
        full_site(), {"grid": ts([1.0]), "pv0": ts([1.0]), "battery": ts([0.0])}
    )
    assert SITE_PV_W not in partial
    assert SITE_HOUSE_W not in partial


def test_no_pv_and_no_battery_house_is_the_grid() -> None:
    site = SiteConfig(grid=GridConfig(power=ref("grid")))
    out = assemble_site_series(site, {"grid": ts([700.0])})
    assert out == {SITE_GRID_W: ts([700.0]), SITE_HOUSE_W: ts([700.0])}


async def test_rebuild_stores_derived_rows_and_reports_missing_ids(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from suncore.ha.connection import HAConnection  # noqa: PLC0415

    class Stats:
        async def statistics_during_period(self, ids, *_args, **_kw):  # type: ignore[no-untyped-def]
            hour = int(T0.timestamp() * 1000)
            rows = {
                "sensor.grid": [{"start": hour, "end": hour + 3_600_000, "mean": -400.0}],
                "sensor.house": [{"start": hour, "end": hour + 3_600_000, "mean": 900.0}],
            }
            return {i: rows[i] for i in ids if i in rows}

    site = SiteConfig(grid=GridConfig(power=ref("grid", invert=True)), house=ref("house"))
    async with await open_timeseries(tmp_path / "t.db") as db:
        store = TimeseriesStore(db)
        connection: HAConnection = Stats()  # type: ignore[assignment]
        result = await rebuild_history(connection, store, site, T0 + timedelta(hours=1), days=1)
        assert result.stored == {SITE_GRID_W: 4, SITE_HOUSE_W: 4}
        assert result.total == 8
        assert result.missing == ()
        grid = await store.read_series(
            SITE_GRID_W, TimeRange(start=T0, end=T0 + timedelta(hours=1))
        )
        assert grid.values == (400.0,) * 4  # invert_sign applied
        assert await db.fetchone("SELECT MIN(derived), MAX(derived) FROM measurement_15m") == (1, 1)
        # the energy of the hour is kept: 900 W x 1 h
        house = await store.read_series(
            SITE_HOUSE_W, TimeRange(start=T0, end=T0 + timedelta(hours=1))
        )
        assert house.total() == pytest.approx(900.0)
