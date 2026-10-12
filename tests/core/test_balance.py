# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import math
from datetime import UTC, datetime

from hypothesis import given
from hypothesis import strategies as st

from suncore.core.balance import compute_flows
from suncore.core.signals import Quality, Sample
from suncore.core.site import SiteConfig
from suncore.core.units import Unit

NOW = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)


def s(value: float | None, unit: Unit | None = Unit.W, q: Quality = Quality.OK) -> Sample:
    return Sample(value, unit, NOW, q)


def r(eid: str, invert: bool = False) -> dict[str, object]:
    return {"entity_id": eid, "invert_sign": invert}


def full_site(*, house: bool = False, invert_grid: bool = False) -> SiteConfig:
    cfg: dict[str, object] = {
        "grid": {"power": r("sensor.grid", invert_grid)},
        "pv": [r("sensor.pv1"), r("sensor.pv2")],
        "battery": {"power": r("sensor.bat"), "soc": r("sensor.soc")},
    }
    if house:
        cfg["house"] = r("sensor.house")
    return SiteConfig.model_validate(cfg)


def samples(grid: float, pv1: float, pv2: float, bat: float) -> dict[str, Sample]:
    return {
        "sensor.grid": s(grid),
        "sensor.pv1": s(pv1),
        "sensor.pv2": s(pv2),
        "sensor.bat": s(bat),
        "sensor.soc": s(55.0, Unit.PERCENT),
    }


def test_basic_derivation() -> None:
    f = compute_flows(full_site(), samples(grid=-500, pv1=2000, pv2=1000, bat=1000))
    assert f.pv_w.value == 3000
    assert f.grid_w.value == -500
    assert f.battery_w.value == 1000
    assert f.house_w.value == 1500  # 3000 - 500 - 1000
    assert f.battery_soc_pct.value == 55
    assert f.house_w.quality is Quality.OK
    assert f.ts == NOW


def test_house_sensor_wins() -> None:
    smp = samples(0, 0, 0, 0) | {"sensor.house": s(1.2, Unit.KW)}
    f = compute_flows(full_site(house=True), smp)
    assert f.house_w.value == 1200


def test_invert_sign_and_kw() -> None:
    smp = samples(grid=0, pv1=0, pv2=0, bat=0) | {"sensor.grid": s(1.5, Unit.KW)}
    f = compute_flows(full_site(invert_grid=True), smp)
    assert f.grid_w.value == -1500
    assert f.house_w.value == -1500  # nonsense input, but arithmetic is consistent


def test_import_export_pair() -> None:
    site = SiteConfig.model_validate(
        {"grid": {"import_power": r("sensor.i"), "export_power": r("sensor.e")}}
    )
    f = compute_flows(site, {"sensor.i": s(0.0), "sensor.e": s(0.4, Unit.KW)})
    assert f.grid_w.value == -400
    f = compute_flows(site, {"sensor.i": s(250.0), "sensor.e": s(0.0)})
    assert f.grid_w.value == 250
    # no PV, no battery -> house == grid
    assert f.pv_w == f.pv_w.__class__(0.0, Quality.OK)
    assert f.battery_w.value == 0.0
    assert f.battery_soc_pct.value is None
    assert f.battery_soc_pct.quality is Quality.UNAVAILABLE
    assert f.house_w.value == 250


def test_missing_sample() -> None:
    smp = samples(100, 0, 0, 0)
    del smp["sensor.grid"]
    f = compute_flows(full_site(), smp)
    assert f.grid_w.value is None
    assert f.grid_w.quality is Quality.UNAVAILABLE
    assert f.house_w.value is None
    assert f.house_w.quality is Quality.UNAVAILABLE


def test_partial_pv_is_unknown() -> None:
    smp = samples(100, 0, 0, 0) | {"sensor.pv2": s(None, Unit.W, Quality.UNAVAILABLE)}
    f = compute_flows(full_site(), smp)
    assert f.pv_w.value is None
    assert f.house_w.value is None


def test_stale_keeps_value_and_propagates() -> None:
    smp = samples(100, 500, 500, 0) | {"sensor.pv1": s(500.0, Unit.W, Quality.STALE)}
    f = compute_flows(full_site(), smp)
    assert f.pv_w.value == 1000
    assert f.pv_w.quality is Quality.STALE
    assert f.house_w.value == 1100
    assert f.house_w.quality is Quality.STALE
    assert f.grid_w.quality is Quality.OK


def test_invalid_inputs() -> None:
    for bad in (
        s(5.0, None),  # unknown unit
        s(5.0, Unit.WH),  # wrong dimension
        s(math.nan),
        s(5.0, Unit.W, Quality.INVALID),
    ):
        f = compute_flows(full_site(), samples(0, 0, 0, 0) | {"sensor.grid": bad})
        assert f.grid_w.value is None
        assert f.grid_w.quality is Quality.INVALID
        assert f.house_w.value is None
        assert f.house_w.quality is Quality.INVALID


def test_soc_must_be_percent() -> None:
    f = compute_flows(full_site(), samples(0, 0, 0, 0) | {"sensor.soc": s(5.0, Unit.W)})
    assert f.battery_soc_pct.value is None
    assert f.battery_soc_pct.quality is Quality.INVALID


def test_ts_is_oldest() -> None:
    old = datetime(2026, 10, 12, 11, 0, tzinfo=UTC)
    smp = samples(0, 0, 0, 0) | {"sensor.pv1": Sample(0.0, Unit.W, old)}
    assert compute_flows(full_site(), smp).ts == old


power = st.floats(min_value=-1e6, max_value=1e6, allow_nan=False)
power_units = st.sampled_from([Unit.W, Unit.KW])
quality = st.sampled_from([Quality.OK, Quality.STALE])


@given(grid=power, pv1=power, pv2=power, bat=power, invert=st.booleans())
def test_balance_closes(grid: float, pv1: float, pv2: float, bat: float, invert: bool) -> None:
    f = compute_flows(full_site(invert_grid=invert), samples(grid, pv1, pv2, bat))
    assert f.grid_w.value is not None
    assert f.pv_w.value is not None
    assert f.battery_w.value is not None
    assert f.house_w.value is not None
    lhs = f.pv_w.value + f.grid_w.value
    rhs = f.house_w.value + f.battery_w.value
    assert math.isclose(lhs, rhs, rel_tol=1e-9, abs_tol=1e-6)
    assert f.grid_w.value == (-grid if invert else grid)


@given(
    qs=st.lists(st.sampled_from(list(Quality)), min_size=4, max_size=4),
    vals=st.lists(power, min_size=4, max_size=4),
)
def test_quality_propagation(qs: list[Quality], vals: list[float]) -> None:
    keys = ["sensor.grid", "sensor.pv1", "sensor.pv2", "sensor.bat"]
    smp = samples(0, 0, 0, 0)
    for key, q, v in zip(keys, qs, vals, strict=True):
        smp[key] = s(None if q is Quality.UNAVAILABLE else v, Unit.W, q)
    f = compute_flows(full_site(), smp)
    expected = Quality.worst(qs)
    if expected.severity >= Quality.UNAVAILABLE.severity:
        assert f.house_w.value is None
    else:
        assert f.house_w.value is not None
    assert f.house_w.quality is expected


@given(
    pair=st.tuples(power, power),
    units=st.tuples(power_units, power_units),
    inv=st.tuples(st.booleans(), st.booleans()),
)
def test_pair_units_and_inversion(
    pair: tuple[float, float], units: tuple[Unit, Unit], inv: tuple[bool, bool]
) -> None:
    site = SiteConfig.model_validate(
        {"grid": {"import_power": r("sensor.i", inv[0]), "export_power": r("sensor.e", inv[1])}}
    )
    f = compute_flows(site, {"sensor.i": s(pair[0], units[0]), "sensor.e": s(pair[1], units[1])})
    imp = pair[0] * units[0].factor * (-1 if inv[0] else 1)
    exp = pair[1] * units[1].factor * (-1 if inv[1] else 1)
    assert f.grid_w.value is not None
    assert math.isclose(f.grid_w.value, imp - exp, rel_tol=1e-9, abs_tol=1e-6)
