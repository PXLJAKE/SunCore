# SPDX-License-Identifier: Apache-2.0
"""Phase 6 PR 6-B storage: ``suncore.db`` 5 -> 6 (``flex_state``), ``timeseries.db`` 2 -> 3
(``grid_event``, ``grid_peak``), their repositories and retention, and the masked MQTT secret."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite
import pytest

from suncore.core.config import (
    SECRET_MASK,
    InverterConfig,
    MqttConfig,
    MqttPasswordIsMaskError,
    OperationConfig,
)
from suncore.core.decisions import OperatingMode
from suncore.core.flex import EvMode, EvState, EvTarget, JobState, ProgramJob, transition
from suncore.core.series import TimeRange
from suncore.core.site import EntityRef
from suncore.storage import ConfigStore, Database, FlexStateStore, GridEvent, open_timeseries
from suncore.storage.errors import SchemaTooNewError
from suncore.storage.flex import FINAL_JOB_KEEP
from suncore.storage.grid import GRID_EVENT_KEEP
from suncore.storage.migrations import latest_version, load_migrations, run_migrations
from suncore.storage.timeseries import TimeseriesStore, timeseries_migrations

NOW = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)


# --- migrations -----------------------------------------------------------------------------


async def test_suncore_db_of_schema_5_upgrades_to_6_and_keeps_its_rows(tmp_path: Path) -> None:
    path = tmp_path / "s.db"
    async with aiosqlite.connect(path, isolation_level=None) as conn:
        assert await run_migrations(conn, load_migrations()[:5]) == 5
        await conn.execute(
            "INSERT INTO config_doc (section, schema_version, body, updated_at) "
            "VALUES ('wizard', 1, '{}', 't')"
        )
    async with await Database.open(path) as db:
        assert await db.user_version() == latest_version() == 6
        tables = {r[0] for r in await db.fetchall("SELECT name FROM sqlite_master")}
        assert "flex_state" in tables
        assert await db.fetchone("SELECT COUNT(*) FROM config_doc") == (1,)


async def test_suncore_db_schema_5_reader_refuses_schema_6(tmp_path: Path) -> None:
    path = tmp_path / "s.db"
    async with await Database.open(path):
        pass
    async with aiosqlite.connect(path, isolation_level=None) as conn:
        with pytest.raises(SchemaTooNewError) as info:
            await run_migrations(conn, load_migrations()[:5])
    assert (info.value.db_version, info.value.code_version) == (6, 5)


async def test_timeseries_db_of_schema_2_upgrades_to_3(tmp_path: Path) -> None:
    path = tmp_path / "ts.db"
    async with aiosqlite.connect(path, isolation_level=None) as conn:
        assert await run_migrations(conn, timeseries_migrations()[:2]) == 2
        await conn.execute(
            "INSERT INTO measurement_15m (key, ts, mean, min, max, n, derived) "
            "VALUES ('site.grid_w', 900, 5.0, 5.0, 5.0, 1, 0)"
        )
    async with await open_timeseries(path) as db:
        assert await db.user_version() == 3
        tables = {r[0] for r in await db.fetchall("SELECT name FROM sqlite_master")}
        assert {"grid_event", "grid_peak"} <= tables
        assert await db.fetchone("SELECT mean FROM measurement_15m") == (5.0,)


async def test_timeseries_schema_2_reader_refuses_schema_3(tmp_path: Path) -> None:
    path = tmp_path / "ts.db"
    async with await open_timeseries(path):
        pass
    async with aiosqlite.connect(path, isolation_level=None) as conn:
        with pytest.raises(SchemaTooNewError):
            await run_migrations(conn, timeseries_migrations()[:2])
    con = sqlite3.connect(path)
    assert con.execute("PRAGMA user_version").fetchone() == (3,)  # left untouched
    con.close()


# --- flex_state -----------------------------------------------------------------------------


@pytest.fixture
async def flex(tmp_path: Path) -> AsyncIterator[FlexStateStore]:
    db = await Database.open(tmp_path / "flex.db")
    clock = {"now": NOW}
    store = FlexStateStore(db, clock=lambda: clock["now"])
    store.clock = clock  # type: ignore[attr-defined]
    yield store
    await db.close()


def _job(job_id: str = "j1", device: str = "washer") -> ProgramJob:
    return ProgramJob(id=job_id, device_id=device, deadline=NOW + timedelta(hours=6), armed_at=NOW)


async def test_ev_state_round_trip_and_update(flex: FlexStateStore) -> None:
    assert await flex.get_ev("wb") is None
    state = EvState(device_id="wb", updated_at=NOW)
    await flex.put_ev(state)
    assert await flex.get_ev("wb") == state
    target = EvTarget(soc_pct=80, by=NOW + timedelta(hours=10))
    newer = state.model_copy(update={"mode": EvMode.TARGET, "target": target})
    await flex.put_ev(newer)
    assert await flex.all_ev() == [newer]


async def test_unreadable_rows_are_skipped(tmp_path: Path) -> None:
    db = await Database.open(tmp_path / "s.db")
    try:
        await db.execute(
            "INSERT INTO flex_state (key, kind, device_id, final, payload, updated_at) "
            "VALUES ('ev:wb', 'ev', 'wb', 0, '{\"mode\": \"turbo\"}', 't'), "
            "('job:x', 'job', 'washer', 0, '{}', 't'), "
            "('ev:other', 'ev', 'other', 0, ?, 't')",
            (EvState(device_id="wb", updated_at=NOW).model_dump_json(),),
        )
        store = FlexStateStore(db)
        assert await store.get_ev("wb") is None  # unreadable = default mode (pv) at runtime
        assert await store.get_ev("other") is None  # payload belongs to another device
        assert await store.jobs() == []
    finally:
        await db.close()


async def test_jobs_open_only_and_prune_of_final_jobs(flex: FlexStateStore) -> None:
    clock = flex.clock  # type: ignore[attr-defined]
    j1, j2 = _job("j1"), _job("j2", device="dryer")
    await flex.put_job(j1)
    await flex.put_job(j2)
    done = transition(transition(j1, JobState.RUNNING, NOW), JobState.DONE, NOW)
    await flex.put_job(done)
    assert [j.id for j in await flex.jobs()] == ["j2"]
    assert [j.id for j in await flex.jobs(open_only=False)] == ["j1", "j2"]
    assert [j.id for j in await flex.jobs("dryer")] == ["j2"]
    assert await flex.get_job("j1") == done
    clock["now"] = NOW + FINAL_JOB_KEEP + timedelta(minutes=1)
    assert await flex.prune() == 1
    assert await flex.get_job("j1") is None
    assert await flex.get_job("j2") == j2  # open jobs are never pruned
    assert await flex.delete_device("dryer") == 1


# --- grid_event / grid_peak -------------------------------------------------------------------


async def test_grid_events_and_retention(tmp_path: Path) -> None:
    async with await open_timeseries(tmp_path / "ts.db") as db:
        store = TimeseriesStore(db)
        old = GridEvent(ts=NOW - GRID_EVENT_KEEP - timedelta(days=1), kind="island_start")
        new = GridEvent(ts=NOW, kind="para14a_start", value_w=4200.0, detail={"source": "relay"})
        await store.grid.record_event(old)
        await store.grid.record_event(new)
        window = TimeRange(start=NOW - timedelta(days=500), end=NOW + timedelta(seconds=1))
        assert await store.grid.events(window) == [old, new]
        report = await store.apply_retention(NOW)
        assert report.grid_event == 1
        assert await store.grid.events(window) == [new]
        with pytest.raises(ValueError, match="finite"):
            await store.grid.record_event(GridEvent(ts=NOW, kind="peak_new", value_w=float("nan")))


async def test_month_peak_only_rises(tmp_path: Path) -> None:
    async with await open_timeseries(tmp_path / "ts.db") as db:
        grid = TimeseriesStore(db).grid
        slot = NOW.replace(minute=0)
        assert await grid.record_quarter("2026-11", slot, 3000.0, NOW)
        assert not await grid.record_quarter("2026-11", slot + timedelta(minutes=15), 2000.0, NOW)
        assert not await grid.record_quarter("2026-11", slot, -5.0, NOW)
        assert not await grid.record_quarter("2026-11", slot, float("inf"), NOW)
        assert await grid.record_quarter("2026-11", slot + timedelta(hours=1), 3500.0, NOW)
        peak = await grid.peak("2026-11")
        assert peak is not None
        assert (peak.peak_w, peak.slot) == (3500.0, slot + timedelta(hours=1))
        await grid.record_quarter("2026-12", slot, 100.0, NOW)
        assert [p.month for p in await grid.peaks()] == ["2026-11", "2026-12"]
        assert await grid.apply_retention(NOW + timedelta(days=4000)) == 0  # peaks stay


# --- config sections: MQTT secret and inverter verification -----------------------------------


def _mqtt(password: str | None = "s3cret") -> MqttConfig:
    return MqttConfig(broker="manual", host="core-mosquitto", username="suncore", password=password)


async def test_mqtt_password_is_stored_but_masked_outside(db: Database) -> None:
    store = ConfigStore(db)
    await store.put("mqtt", _mqtt())
    raw = await db.fetchone("SELECT body FROM config_doc WHERE section = 'mqtt'")
    assert raw is not None
    assert json.loads(str(raw[0]))["password"] == "s3cret"
    loaded = await store.get("mqtt")
    assert isinstance(loaded, MqttConfig)
    assert loaded.password is not None
    assert loaded.password.get_secret_value() == "s3cret"
    assert loaded.model_dump(mode="json")["password"] == SECRET_MASK  # the API's view
    exported = await store.export_json()
    assert exported["sections"]["mqtt"]["body"]["password"] == SECRET_MASK
    # The UI sends the mask back: the stored secret is kept, and the history masks it too.
    await store.put("mqtt", _mqtt(SECRET_MASK).model_copy(update={"port": 8883}))
    kept = await store.get("mqtt")
    assert isinstance(kept, MqttConfig)
    assert kept.port == 8883
    assert kept.password is not None
    assert kept.password.get_secret_value() == "s3cret"
    history = await store.history("mqtt")
    assert history[0].body["password"] == SECRET_MASK


async def test_import_of_a_masked_export_keeps_or_drops_the_secret(tmp_path: Path) -> None:
    async with (
        await Database.open(tmp_path / "a.db") as a,
        await Database.open(tmp_path / "b.db") as b,
    ):
        source, target = ConfigStore(a), ConfigStore(b)
        await source.put("mqtt", _mqtt())
        exported = await source.export_json()
        await target.import_json(exported)  # nothing stored in the target: secret gone
        fresh = await target.get("mqtt")
        assert isinstance(fresh, MqttConfig)
        assert fresh.password is None
        await source.import_json(exported)  # same system: the stored secret is kept
        same = await source.get("mqtt")
        assert isinstance(same, MqttConfig)
        assert same.password is not None
        assert same.password.get_secret_value() == "s3cret"


async def test_import_never_carries_the_inverter_verification(db: Database) -> None:
    store = ConfigStore(db)
    verified = InverterConfig(
        adapter="kostal_modbus", capability="ac_limit", nominal_ac_w=10_000, limit_verified=True
    )
    await store.put("inverter", verified)
    await store.import_json(await store.export_json())
    loaded = await store.get("inverter")
    assert isinstance(loaded, InverterConfig)
    assert loaded.limit_verified is False
    assert loaded.mode_capped


async def test_a_new_inverter_adapter_is_unverified_and_back_in_shadow(db: Database) -> None:
    """PR 6-G: a changed adapter identity resets ``limit_verified`` and the inverter mode."""
    store = ConfigStore(db)
    first = InverterConfig(
        adapter="generic_number",
        capability="export_limit",
        limit_entity=EntityRef(entity_id="number.inverter_export_limit"),
        limit_verified=True,
    )
    await store.put("inverter", first)
    await store.put("operation", OperationConfig(inverter_mode=OperatingMode.ASSISTED))
    # The same adapter (a setting changed): verification and mode stay.
    same = await store.put("inverter", first.model_copy(update={"enforce_static": True}))
    assert isinstance(same, InverterConfig)
    assert same.limit_verified
    kept = await store.get("operation")
    assert isinstance(kept, OperationConfig)
    assert kept.inverter_mode is OperatingMode.ASSISTED
    # Another limit entity is another adapter: never verified, never inherits the mode.
    other = first.model_copy(
        update={"limit_entity": EntityRef(entity_id="number.other_export_limit")}
    )
    changed = await store.put("inverter", other)
    assert isinstance(changed, InverterConfig)
    assert changed.limit_verified is False
    dropped = await store.get("operation")
    assert isinstance(dropped, OperationConfig)
    assert dropped.inverter_mode is OperatingMode.SHADOW


async def test_import_drops_the_inverter_mode(db: Database) -> None:
    store = ConfigStore(db)
    await store.put("operation", OperationConfig(inverter_mode=OperatingMode.AUTOMATIC))
    await store.import_json(await store.export_json())
    loaded = await store.get("operation")
    assert isinstance(loaded, OperationConfig)
    assert loaded.inverter_mode is OperatingMode.SHADOW


async def test_a_mask_without_a_stored_password_is_rejected(db: Database) -> None:
    """Review H3: the mask as a new password raises instead of being dropped."""
    store = ConfigStore(db)
    with pytest.raises(MqttPasswordIsMaskError):
        await store.put("mqtt", _mqtt(SECRET_MASK))
    assert await store.get("mqtt") is None
    await store.put("mqtt", _mqtt(None))
    with pytest.raises(MqttPasswordIsMaskError):
        await store.put("mqtt", _mqtt(SECRET_MASK))
