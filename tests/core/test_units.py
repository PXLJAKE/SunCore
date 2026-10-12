# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from suncore.core.units import (
    Dimension,
    Unit,
    apply_invert,
    convert,
    from_base,
    house_load_w,
    is_charging,
    is_discharging,
    is_exporting,
    is_importing,
    net_grid_w,
    parse_unit,
    to_base,
)

finite = st.floats(min_value=-1e9, max_value=1e9, allow_nan=False)


@pytest.mark.parametrize(
    ("text", "unit"),
    [
        ("W", Unit.W),
        ("kW", Unit.KW),
        ("Wh", Unit.WH),
        ("kWh", Unit.KWH),
        ("%", Unit.PERCENT),
        (" kW ", Unit.KW),
    ],
)
def test_parse_known(text: str, unit: Unit) -> None:
    assert parse_unit(text) is unit


@pytest.mark.parametrize("text", [None, "", "KW", "mW", "MW", "var", "°C", "w", "kwh"])
def test_parse_unknown_is_none(text: str | None) -> None:
    assert parse_unit(text) is None


def test_base_conversion() -> None:
    assert to_base(1.5, Unit.KW) == 1500.0
    assert to_base(2.0, Unit.KWH) == 2000.0
    assert to_base(42.0, Unit.W) == 42.0
    assert to_base(80.0, Unit.PERCENT) == 80.0
    assert Unit.KW.base is Unit.W
    assert Unit.KWH.dimension is Dimension.ENERGY


def test_convert_dimension_mismatch() -> None:
    with pytest.raises(ValueError, match="different dimensions"):
        convert(1.0, Unit.KW, Unit.KWH)


@given(finite, st.sampled_from(list(Unit)))
def test_round_trip(value: float, unit: Unit) -> None:
    assert math.isclose(from_base(to_base(value, unit), unit), value, rel_tol=1e-12, abs_tol=1e-9)


@given(finite)
def test_kw_w_round_trip(value: float) -> None:
    back = convert(convert(value, Unit.KW, Unit.W), Unit.W, Unit.KW)
    assert math.isclose(back, value, rel_tol=1e-12, abs_tol=1e-9)


def test_sign_helpers() -> None:
    assert apply_invert(5.0, True) == -5.0
    assert apply_invert(5.0, False) == 5.0
    assert net_grid_w(300.0, 0.0) == 300.0
    assert net_grid_w(0.0, 300.0) == -300.0
    assert is_importing(1.0)
    assert not is_importing(0.0)
    assert not is_importing(-1.0)
    assert is_exporting(-1.0)
    assert not is_exporting(0.0)
    assert is_charging(1.0)
    assert not is_charging(-1.0)
    assert is_discharging(-1.0)
    assert not is_discharging(0.0)
    # 3 kW PV, 1 kW exported, 500 W charging -> house 1.5 kW
    assert house_load_w(3000.0, -1000.0, 500.0) == 1500.0
