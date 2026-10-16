# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from suncore.core.balance import compute_flows
from suncore.core.signals import Quality
from suncore.core.site import SiteConfig
from suncore.core.units import Unit
from suncore.ha.cache import EntityCache, parse_numeric
from suncore.ha.connection import EntityState, EntityUpdate

T0 = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def _st(
    entity_id: str, state: str, unit: str | None = "W", reported: float | None = None
) -> EntityState:
    attrs: dict[str, Any] = {} if unit is None else {"unit_of_measurement": unit}
    return EntityState(entity_id, state, attrs, last_reported=reported)


def _cache(clock: Clock, **kw: Any) -> EntityCache:
    return EntityCache(clock=clock, **kw)


@pytest.mark.parametrize(
    ("text", "expected"),
    [("12", 12.0), ("-3.5", -3.5), (" 7 ", 7.0), ("1e3", 1000.0), (".5", 0.5), ("+2", 2.0)],
)
def test_parse_numeric_accepts(text: str, expected: float) -> None:
    assert parse_numeric(text) == expected


@pytest.mark.parametrize(
    "text", ["", "abc", "nan", "inf", "-infinity", "1_000", "1,5", "0x10", "1e999"]
)
def test_parse_numeric_rejects(text: str) -> None:
    assert parse_numeric(text) is None


def test_quality_mapping() -> None:
    clock = Clock()
    cache = _cache(clock)
    cache.ingest(
        EntityUpdate(
            changed={
                "sensor.ok": _st("sensor.ok", "1500.5"),
                "sensor.na": _st("sensor.na", "unavailable"),
                "sensor.unk": _st("sensor.unk", "unknown"),
                "sensor.text": _st("sensor.text", "charging"),
                "sensor.nounit": _st("sensor.nounit", "5", unit=None),
                "sensor.kw": _st("sensor.kw", "1.5", unit="kW"),
            },
            full=True,
        )
    )
    s = cache.samples()
    assert (s["sensor.ok"].value, s["sensor.ok"].unit, s["sensor.ok"].quality) == (
        1500.5,
        Unit.W,
        Quality.OK,
    )
    assert s["sensor.ok"].ts == T0
    assert (s["sensor.na"].value, s["sensor.na"].quality) == (None, Quality.UNAVAILABLE)
    assert s["sensor.unk"].quality is Quality.UNAVAILABLE
    assert (s["sensor.text"].value, s["sensor.text"].quality) == (None, Quality.INVALID)
    assert (s["sensor.nounit"].unit, s["sensor.nounit"].value) == (None, 5.0)
    assert s["sensor.kw"].unit is Unit.KW
    assert cache.sample("sensor.missing") is None
    assert set(cache.samples(["sensor.ok", "sensor.missing"])) == {"sensor.ok"}


def test_staleness_per_entity_with_injected_clock() -> None:
    clock = Clock()
    cache = _cache(clock, max_age_s=120, max_age_overrides={"sensor.slow": 600})
    cache.ingest(
        EntityUpdate(
            changed={"sensor.a": _st("sensor.a", "1"), "sensor.slow": _st("sensor.slow", "2")}
        )
    )
    clock.advance(120)
    assert not cache.is_stale("sensor.a")  # exactly at max age is still fresh
    clock.advance(1)
    assert cache.is_stale("sensor.a")
    assert not cache.is_stale("sensor.slow")
    sample = cache.sample("sensor.a")
    assert sample is not None
    assert (sample.value, sample.quality) == (1.0, Quality.STALE)
    assert cache.sample("sensor.slow") is not None
    assert cache.is_stale("sensor.unknown")
    assert cache.age_s("sensor.a") == 121

    cache.ingest(EntityUpdate(changed={"sensor.a": _st("sensor.a", "1")}))  # event refreshes
    assert not cache.is_stale("sensor.a")


def test_unavailable_beats_stale_and_invalid_beats_all() -> None:
    clock = Clock()
    cache = _cache(clock, max_age_s=10)
    cache.ingest(
        EntityUpdate(
            changed={"sensor.na": _st("sensor.na", "unavailable"), "sensor.t": _st("sensor.t", "x")}
        )
    )
    clock.advance(100)
    na, t = cache.sample("sensor.na"), cache.sample("sensor.t")
    assert na is not None
    assert t is not None
    assert na.quality is Quality.UNAVAILABLE
    assert t.quality is Quality.INVALID


def test_full_update_refreshes_only_on_content_change() -> None:
    clock = Clock()
    cache = _cache(clock, max_age_s=100)
    cache.ingest_states([_st("sensor.a", "0", reported=10.0), _st("sensor.b", "0", reported=10.0)])
    clock.advance(90)
    # resync: a reported again (last_reported moved), b did not
    cache.ingest_states([_st("sensor.a", "0", reported=100.0), _st("sensor.b", "0", reported=10.0)])
    clock.advance(20)
    assert not cache.is_stale("sensor.a")  # refreshed 20 s ago
    assert cache.is_stale("sensor.b")  # last proof of life 110 s ago
    # new entity in a full update is fresh; vanished ones are dropped
    cache.ingest_states([_st("sensor.c", "1")])
    assert "sensor.a" not in cache
    assert len(cache) == 1
    assert not cache.is_stale("sensor.c")


def test_removed_in_incremental_update() -> None:
    clock = Clock()
    cache = _cache(clock)
    cache.ingest(EntityUpdate(changed={"sensor.a": _st("sensor.a", "1")}))
    cache.ingest(EntityUpdate(removed=("sensor.a", "sensor.nope")))
    assert cache.get("sensor.a") is None


def test_rejects_bad_config() -> None:
    with pytest.raises(ValueError, match="max_age_s"):
        EntityCache(max_age_s=0)
    naive = EntityCache(clock=lambda: datetime(2026, 1, 1))  # noqa: DTZ001
    with pytest.raises(ValueError, match="timezone-aware"):
        naive.samples()


def test_samples_feed_compute_flows() -> None:
    clock = Clock()
    cache = _cache(clock, max_age_s=60)
    site = SiteConfig.model_validate(
        {
            "grid": {"power": {"entity_id": "sensor.grid"}},
            "pv": [{"name": "PV", "power": {"entity_id": "sensor.pv"}}],
            "battery": {"power": {"entity_id": "sensor.bat"}, "soc": {"entity_id": "sensor.soc"}},
        }
    )
    cache.ingest_states(
        [
            _st("sensor.grid", "-0.5", unit="kW"),
            _st("sensor.pv", "3000"),
            _st("sensor.bat", "1000"),
            _st("sensor.soc", "40", unit="%"),
        ]
    )
    flows = compute_flows(site, cache.samples())
    assert flows.house_w.value == pytest.approx(3000 - 500 - 1000)
    assert flows.house_w.quality is Quality.OK
    clock.advance(61)
    flows = compute_flows(site, cache.samples())
    assert flows.house_w.quality is Quality.STALE
    cache.ingest(EntityUpdate(changed={"sensor.pv": _st("sensor.pv", "unavailable")}))
    flows = compute_flows(site, cache.samples())
    assert flows.pv_w.quality is Quality.UNAVAILABLE
    assert flows.house_w.value is None


def test_only_heartbeats_turn_stale_when_silent() -> None:
    """Integrations that report only changes stay valid; the grid meter must keep reporting."""
    clock = Clock()
    cache = _cache(clock, max_age_s=120)
    cache.ingest(
        EntityUpdate(
            changed={
                "sensor.grid": _st("sensor.grid", "-500"),
                "sensor.soc": _st("sensor.soc", "84", unit="%"),
            }
        )
    )
    cache.set_heartbeats(["sensor.grid"])
    clock.advance(25 * 60)  # 25 minutes at 84 %, as on the maintainer's battery
    soc = cache.sample("sensor.soc")
    grid = cache.sample("sensor.grid")
    assert soc is not None
    assert grid is not None
    assert (soc.value, soc.quality) == (84.0, Quality.OK)
    assert grid.quality is Quality.STALE
    assert not cache.is_stale("sensor.soc")
    assert cache.is_stale("sensor.grid")
    assert cache.age_s("sensor.soc") == 25 * 60  # the age is still known
    cache.set_heartbeats(None)  # back to judging every entity
    soc = cache.sample("sensor.soc")
    assert soc is not None
    assert soc.quality is Quality.STALE
