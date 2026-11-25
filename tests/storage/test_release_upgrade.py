# SPDX-License-Identifier: Apache-2.0
"""Upgrade from the released 0.2.0 to the current schema (WP13).

What is frozen and where it comes from:

- `tests/fixtures/db/suncore_v1.sql` and `tests/fixtures/release/export_0.2.0.json` are produced
  by `scripts/gen_release_fixtures.py` from the tag `v0.2.0` (seeded through the released API),
  never by hand. `MANIFEST_<tag>.json` records the tag, the commit and the SHA-256 of every
  migration file of the tag.
- No other tag exists. `suncore.db` v2 ... v6 and `timeseries.db` v1 ... v3 were never released,
  so they have no frozen dump: the tests for them build the old schema from the shipped
  migration files (`tests/storage/test_phase5_storage.py`) and this file only adds the
  released-to-current chain. Once a release ships schema N, add its dump with the generator.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from suncore.core.config import ForecastConfig, GoalsConfig, TariffConfig
from suncore.core.site import SiteConfig
from suncore.storage import (
    ConfigStore,
    Database,
    DecisionLog,
    FlexStateStore,
    GridStore,
    LearnedParamStore,
    PlanSnapshotStore,
    PowerModelStateStore,
    SchemaTooNewError,
    SqliteRuntimeStore,
    open_timeseries,
)
from suncore.storage.backtests import BacktestRunStore
from suncore.storage.config_store import DEFAULT_SECTIONS
from suncore.storage.migrations import latest_version, load_migrations
from suncore.storage.summaries import DaySummaryStore
from suncore.storage.timeseries import timeseries_migrations

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
MANIFEST: dict[str, Any] = json.loads(
    (FIXTURES / "release" / "MANIFEST_v0.2.0.json").read_text("utf-8")
)
EXPORT_020: dict[str, Any] = json.loads(
    (FIXTURES / "release" / f"export_{MANIFEST['app_version']}.json").read_text("utf-8")
)
DUMP_V1 = FIXTURES / "db" / "suncore_v1.sql"


def _restore(dump: Path, target: Path) -> Path:
    """Recreate a database file from a frozen dump (the dump ends with its user_version)."""
    con = sqlite3.connect(target)
    con.executescript(dump.read_text("utf-8"))
    con.close()
    return target


def _contains(new: Any, old: Any) -> bool:
    """`new` holds everything in `old` (dicts may have gained keys, lists keep their length)."""
    if isinstance(old, dict):
        return isinstance(new, dict) and all(
            k in new and _contains(new[k], v) for k, v in old.items()
        )
    if isinstance(old, list):
        return (
            isinstance(new, list)
            and len(new) == len(old)
            and all(_contains(n, o) for n, o in zip(new, old, strict=True))
        )
    return bool(new == old)


# --- the frozen fixtures are what the tag wrote -------------------------------------------------


def test_manifest_matches_the_released_schema() -> None:
    assert MANIFEST["tag"] == "v0.2.0"
    assert MANIFEST["suncore_db_schema"] == 1
    assert EXPORT_020["db_schema_version"] == 1
    assert EXPORT_020["app_version"] == MANIFEST["app_version"] == "0.2.0"


_RELEASED_MIGRATIONS = [
    (m["tag"], name, digest)
    for path in sorted((FIXTURES / "release").glob("MANIFEST_*.json"))
    for m in [json.loads(path.read_text("utf-8"))]
    for name, digest in m["migrations_sha256"].items()
]


@pytest.mark.parametrize(("tag", "name", "digest"), _RELEASED_MIGRATIONS)
def test_released_migration_files_are_unchanged(tag: str, name: str, digest: str) -> None:
    """A migration that shipped must never be edited: a new file is added instead."""
    shipped = next(m for m in load_migrations() if m.name == name)
    assert hashlib.sha256(shipped.sql.encode("utf-8")).hexdigest() == digest, tag


def test_frozen_dump_is_a_schema_1_database(tmp_path: Path) -> None:
    path = _restore(DUMP_V1, tmp_path / "suncore.db")
    con = sqlite3.connect(path)
    assert con.execute("PRAGMA user_version").fetchone() == (1,)
    assert con.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"config_doc", "decision", "learned"} <= names
    assert "config_history" not in names  # a 0.2.0 database really is schema 1
    con.close()


# --- 0.2.0 database -> current code ---------------------------------------------------------


async def test_v1_database_migrates_to_current_and_keeps_every_row(tmp_path: Path) -> None:
    path = _restore(DUMP_V1, tmp_path / "suncore.db")
    async with await Database.open(path) as db:
        assert await db.user_version() == latest_version()
        # the released rows are intact
        log = DecisionLog(db)
        assert await log.count() == 3
        latest = await log.latest(10)
        assert [d.intent.id for d in latest] == ["seed-2", "seed-1", "seed-0"]
        assert {d.kind.value for d in latest} == {"shadow"}
        assert {d.mode.value for d in latest} == {"shadow"}
        assert await db.fetchone("SELECT value FROM learned WHERE key = 'seed.note'") == (
            '{"n": 1}',
        )
        site = await ConfigStore(db).get_site()
        assert site is not None
        assert [a.name for a in site.pv] == ["PV 1"]  # v1 -> v2 gave the array a name
        assert site.pv[0].power.entity_id == "sensor.sim_pv_power"
        assert site.battery is not None
        # the stored document is only rewritten on a put, the history stays empty
        assert await db.fetchone("SELECT schema_version FROM config_doc WHERE section='site'") == (
            1,
        )
        assert await db.fetchone("SELECT COUNT(*) FROM config_history") == (0,)


async def test_every_table_of_the_current_schema_reads_through_its_repository(
    tmp_path: Path,
) -> None:
    """After the upgrade each repository answers (empty where 0.2.0 had no such data)."""
    path = _restore(DUMP_V1, tmp_path / "suncore.db")
    async with await Database.open(path) as db:
        tables = {
            str(r[0])
            for r in await db.fetchall(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        config = ConfigStore(db)
        assert await config.stored_sections() == frozenset({"site"})  # config_doc
        assert await config.history("site") == []  # config_history
        assert await DecisionLog(db).count() == 3  # decision
        assert list(await SqliteRuntimeStore(db).all_runtimes()) == []  # device_runtime
        assert await SqliteRuntimeStore(db).load_emergency_stop() is False  # safety_state
        assert await PowerModelStateStore(db).get("none") is None  # power_model_state
        assert await PlanSnapshotStore(db).latest() is None  # plan_snapshot
        assert await LearnedParamStore(db).in_namespace("forecast_bias") == []  # learned_param
        assert await BacktestRunStore(db).headers() == []  # backtest_run
        assert await FlexStateStore(db).all_ev() == []  # flex_state
        assert await db.fetchone("SELECT COUNT(*) FROM learned") == (1,)  # learned (v1 table)
    assert tables == {
        "config_doc",
        "config_history",
        "decision",
        "learned",
        "device_runtime",
        "safety_state",
        "power_model_state",
        "plan_snapshot",
        "learned_param",
        "backtest_run",
        "flex_state",
    }


async def test_timeseries_database_is_created_next_to_an_upgraded_suncore_db(
    tmp_path: Path,
) -> None:
    """0.2.0 had no timeseries.db: the first start with the new code creates it empty."""
    async with await open_timeseries(tmp_path / "timeseries.db") as ts:
        assert await ts.user_version() == len(timeseries_migrations())
        assert await DaySummaryStore(ts).count() == 0
        assert await GridStore(ts).peaks() == []
        assert await DaySummaryStore(ts).between(date(2026, 1, 1), date(2026, 12, 31)) == []


async def test_upgrade_is_idempotent(tmp_path: Path) -> None:
    path = _restore(DUMP_V1, tmp_path / "suncore.db")
    async with await Database.open(path) as db:
        await db.execute("INSERT INTO learned VALUES ('after', '2', 't')")
    async with await Database.open(path) as db:
        assert await db.user_version() == latest_version()
        assert await db.fetchone("SELECT COUNT(*) FROM learned") == (2,)
        assert await DecisionLog(db).count() == 3


# --- the 0.2.0 export imports into the current version ----------------------------------------


async def test_export_of_0_2_0_imports_into_the_current_version(tmp_path: Path) -> None:
    async with await Database.open(tmp_path / "suncore.db") as db:
        store = ConfigStore(db)
        assert await store.import_json(EXPORT_020) == ["site"]
        site = await store.get_site()
        assert site is not None
        assert [a.name for a in site.pv] == ["PV 1"]
        again = await store.export_json()
        assert again["db_schema_version"] == latest_version()
        site_version = DEFAULT_SECTIONS["site"].current_version
        assert again["sections"]["site"]["schema_version"] == site_version
        # nothing 0.2.0 exported is lost: later versions only add defaulted fields and name the
        # PV arrays
        old = EXPORT_020["sections"]["site"]["body"]
        new = again["sections"]["site"]["body"]
        for key in ("grid", "battery", "house"):
            assert _contains(new[key], old[key]), key
        assert [a["power"] for a in new["pv"]] == old["pv"]


async def test_import_never_inherits_modes_from_0_2_0(tmp_path: Path) -> None:
    """0.2.0 knew only shadow: after the import there are no stored modes or devices."""
    async with await Database.open(tmp_path / "suncore.db") as db:
        store = ConfigStore(db)
        await store.import_json(EXPORT_020)
        assert await store.get("operation") is None
        assert await store.get("devices") is None


# --- newer than supported is refused, untouched -------------------------------------------------


async def test_database_from_a_newer_release_is_refused_and_left_alone(tmp_path: Path) -> None:
    path = _restore(DUMP_V1, tmp_path / "suncore.db")
    newer = latest_version() + 1
    con = sqlite3.connect(path)
    con.execute(f"PRAGMA user_version = {newer}")
    con.close()
    with pytest.raises(SchemaTooNewError) as info:
        await Database.open(path)
    assert str(info.value) == (
        f"database schema version {newer} is newer than the supported {latest_version()}"
    )
    assert (info.value.db_version, info.value.code_version) == (newer, latest_version())
    con = sqlite3.connect(path)
    assert con.execute("PRAGMA user_version").fetchone() == (newer,)
    assert con.execute("SELECT COUNT(*) FROM decision").fetchone() == (3,)
    con.close()


def test_the_process_refuses_a_newer_database_and_exits_non_zero(tmp_path: Path) -> None:
    """`python -m suncore` on a /data of a newer release: exit 1, message logged, file intact."""
    data = tmp_path / "data"
    data.mkdir()
    newer = latest_version() + 1
    con = sqlite3.connect(data / "suncore.db")
    con.execute(f"PRAGMA user_version = {newer}")
    con.close()
    env = {
        **os.environ,
        "SUNCORE_DATA_DIR": str(data),
        "SUNCORE_OPTIONS_PATH": str(tmp_path / "none.json"),
        "SUNCORE_STATIC_DIR": str(tmp_path / "no-dist"),
        "SUNCORE_PORT": "65000",
        "SUNCORE_HOST": "127.0.0.1",
        "SUPERVISOR_TOKEN": "",
    }
    env.pop("SUNCORE_DEMO", None)
    done = subprocess.run(
        [sys.executable, "-m", "suncore"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    output = done.stdout + done.stderr
    assert done.returncode == 1, output
    assert f"database schema version {newer} is newer than the supported" in output
    con = sqlite3.connect(data / "suncore.db")
    assert con.execute("PRAGMA user_version").fetchone() == (newer,)
    con.close()


async def test_timeseries_from_a_newer_release_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "timeseries.db"
    newer = len(timeseries_migrations()) + 1
    con = sqlite3.connect(path)
    con.execute(f"PRAGMA user_version = {newer}")
    con.close()
    with pytest.raises(SchemaTooNewError):
        await open_timeseries(path)


# --- section migrations: every old version, random valid documents -----------------------------

_ids = st.from_regex(r"[a-z][a-z0-9]{0,6}", fullmatch=True)
_entity = st.builds(lambda d, o: f"{d}.{o}", _ids, _ids)
_ref = st.fixed_dictionaries({"entity_id": _entity, "invert_sign": st.booleans()})


@st.composite
def _site_v1(draw: st.DrawFn) -> dict[str, Any]:
    # A site must not use an entity twice, so every reference draws from one unique pool.
    pool = iter(draw(st.lists(_entity, min_size=9, max_size=9, unique=True)))

    def ref() -> dict[str, Any]:
        return {"entity_id": next(pool), "invert_sign": draw(st.booleans())}

    grid = (
        {"power": None, "import_power": ref(), "export_power": ref()}
        if draw(st.booleans())
        else {"power": ref(), "import_power": None, "export_power": None}
    )
    battery = None
    if draw(st.booleans()):  # a state of charge cannot be inverted
        battery = {"power": ref(), "soc": {**ref(), "invert_sign": False}}
    return {
        "schema_version": 1,
        "grid": grid,
        "pv": [ref() for _ in range(draw(st.integers(0, 4)))],
        "battery": battery,
        "house": ref() if draw(st.booleans()) else None,
    }


_forecast_roles = st.dictionaries(
    st.sampled_from(["forecast_today", "forecast_tomorrow"]), _ref, max_size=2
)
_forecast_v1_docs = st.fixed_dictionaries(
    {
        "schema_version": st.just(1),
        "forecast_template_ids": st.lists(st.just("solcast.pv"), max_size=1),
        "forecast_entities": _forecast_roles,
    }
)
_price = st.floats(min_value=0, max_value=2, allow_nan=False)
_tariff_v1_docs = st.fixed_dictionaries(
    {
        "schema_version": st.just(1),
        "import": st.fixed_dictionaries({"kind": st.just("fixed"), "fixed_price": _price}),
        "export": st.fixed_dictionaries({"kind": st.just("fixed"), "fixed_price": _price}),
        "forecast_template_ids": st.lists(st.just("solcast.pv"), max_size=1),
        "forecast_entities": _forecast_roles,
    }
)
_goals_v1_docs = st.fixed_dictionaries(
    {
        "schema_version": st.just(1),
        "battery_target": st.none()
        | st.fixed_dictionaries(
            {
                "soc_pct": st.floats(min_value=10, max_value=100, allow_nan=False),
                "time": st.sampled_from(["06:00", "18:00", "22:30"]),
            }
        ),
        "climate_before_export": st.booleans(),
        "grid_charge_night_only": st.booleans(),
    }
)


def _upgrade(section: str, version: int, body: dict[str, Any]) -> Any:
    """What `ConfigStore._upgrade` does: the migrations from `version`, then validation."""
    spec = DEFAULT_SECTIONS[section]
    for step in spec.migrations[version - 1 :]:
        body = step(body)
    return spec.model.model_validate({**body, "schema_version": spec.current_version})


_PROPS = settings(
    max_examples=60,
    deadline=None,
    derandomize=True,  # deterministic seeds in CI
    suppress_health_check=[HealthCheck.too_slow],
)


@_PROPS
@given(_site_v1())
def test_any_valid_site_v1_document_migrates(doc: dict[str, Any]) -> None:
    site = _upgrade("site", 1, json.loads(json.dumps(doc)))
    assert isinstance(site, SiteConfig)
    assert [a.power.model_dump() for a in site.pv] == doc["pv"]  # order and content kept
    assert len({a.name for a in site.pv}) == len(site.pv)  # names are unique
    assert site.schema_version == DEFAULT_SECTIONS["site"].current_version


@_PROPS
@given(_forecast_v1_docs)
def test_any_valid_forecast_v1_document_migrates(doc: dict[str, Any]) -> None:
    forecast = _upgrade("forecast", 1, json.loads(json.dumps(doc)))
    assert isinstance(forecast, ForecastConfig)
    assert forecast.template_ids == doc["forecast_template_ids"]
    for source in forecast.sources:  # every source receives the whole v1 role map
        assert set(source.entities) == set(doc["forecast_entities"])


@_PROPS
@given(_tariff_v1_docs)
def test_any_valid_tariff_v1_document_migrates(doc: dict[str, Any]) -> None:
    tariff = _upgrade("tariff", 1, json.loads(json.dumps(doc)))
    assert isinstance(tariff, TariffConfig)
    assert tariff.import_.fixed_price == doc["import"]["fixed_price"]
    assert "forecast_template_ids" not in tariff.model_dump()  # moved to the forecast section


@_PROPS
@given(_goals_v1_docs)
def test_any_valid_goals_v1_document_migrates(doc: dict[str, Any]) -> None:
    goals = _upgrade("goals", 1, json.loads(json.dumps(doc)))
    assert isinstance(goals, GoalsConfig)
    assert (goals.grid_charge_schedule is not None) == doc["grid_charge_night_only"]
    assert goals.climate_before_export == doc["climate_before_export"]


def test_every_migrating_section_has_a_property_test() -> None:
    migrating = {name for name, spec in DEFAULT_SECTIONS.items() if spec.migrations}
    assert migrating == {"site", "forecast", "tariff", "goals"}, (
        "a section gained a migration: add a hypothesis test with its old document shape"
    )
