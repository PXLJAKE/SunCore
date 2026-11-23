# SPDX-License-Identifier: Apache-2.0
"""Random planner situations for the property tests (hypothesis strategies)."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timedelta

from hypothesis import strategies as st

from suncore.core.battery import BatteryStrategy, GridChargePolicy, RiskLevel
from suncore.core.config import BatteryTarget, GoalsConfig
from suncore.core.tariff import WeeklySchedule
from suncore.sim.planner_scenarios import (
    SUMMER,
    TZ,
    WINTER,
    Situation,
    climate,
    tibber_price,
)

PriceFn = Callable[[datetime], float | None]


def _hour(at: datetime) -> float:
    local = at.astimezone(TZ)
    return local.hour + local.minute / 60


def price_pattern(kind: str, level: float) -> tuple[PriceFn, PriceFn]:  # noqa: PLR0911
    """Import and export price functions of a named pattern."""

    def export(_at: datetime) -> float:
        return 0.08

    if kind == "flat":
        return (lambda _at: level), export
    if kind == "negative":

        def imp(at: datetime) -> float:
            return -0.05 if 11 <= _hour(at) < 15 else tibber_price(at)

        def exp(at: datetime) -> float:
            return 0.0 if imp(at) < 0 else 0.08

        return imp, exp
    if kind == "spike":
        return (lambda at: 1.2 if 18 <= _hour(at) < 19.5 else level), export
    if kind == "night_cheap":
        return (lambda at: 0.12 if 1 <= _hour(at) < 5 else level), export
    if kind == "partial":

        def partial(at: datetime) -> float | None:
            start = datetime(2026, 1, 1, tzinfo=TZ)
            known = (at - start) % timedelta(days=2) < timedelta(hours=30)
            return tibber_price(at) if known else None

        return partial, export
    if kind == "negative_export":
        return tibber_price, (lambda at: -0.03 if 12 <= _hour(at) < 14 else 0.08)
    return tibber_price, export


@st.composite
def situations(draw: st.DrawFn) -> Situation:
    summer = draw(st.booleans())
    day = SUMMER if summer else WINTER
    hour = draw(st.integers(0, 23))
    minute = draw(st.sampled_from([0, 7, 15, 44]))
    now = datetime(day.year, day.month, day.day, hour, minute, tzinfo=TZ)
    kind = draw(
        st.sampled_from(
            ["tibber", "flat", "negative", "spike", "night_cheap", "partial", "negative_export"]
        )
    )
    level = draw(st.floats(0.15, 0.45))
    imp, exp = price_pattern(kind, level)
    policy = draw(
        st.sampled_from(
            [
                GridChargePolicy(),
                GridChargePolicy(enabled=True, max_price=0.2),
                GridChargePolicy(enabled=True, schedule=WeeklySchedule.daily("00:00", "06:00")),
                GridChargePolicy(
                    enabled=True,
                    max_price=0.25,
                    schedule=WeeklySchedule.daily("22:00", "05:00"),
                ),
            ]
        )
    )
    n_dev = draw(st.integers(0, 2))
    values = [draw(st.one_of(st.none(), st.floats(0.0, 0.5))) for _ in range(n_dev)]
    devices = tuple(
        climate(f"dev{i}", draw(st.floats(300, 2500)), value) for i, value in enumerate(values)
    )
    target = draw(
        st.one_of(
            st.none(),
            st.builds(
                BatteryTarget,
                soc_pct=st.floats(20, 100),
                time=st.sampled_from(["07:00", "12:30", "18:00", "21:45"]),
            ),
        )
    )
    situation = Situation(
        now=now,
        kwp=draw(st.floats(0.0, 20.0)),
        summer=summer,
        day_factors=tuple(draw(st.lists(st.floats(0.05, 1.0), min_size=3, max_size=3))),
        cloud=draw(st.sampled_from([None, lambda at: 0.4 + 0.6 * abs(math.sin(_hour(at)))])),
        p10_share=draw(st.floats(0.2, 1.0)),
        load_base_w=draw(st.floats(100, 1500)),
        import_price=imp,
        export_price=exp,
        capacity_wh=draw(st.floats(2_000, 40_000)),
        max_power_w=draw(st.floats(500, 12_000)),
        soc_pct=draw(st.floats(0, 100)),
        strategy=draw(st.sampled_from(list(BatteryStrategy))),
        risk=draw(st.sampled_from(list(RiskLevel))),
        grid_charge=policy,
        goals=GoalsConfig(battery_target=target),
        devices=devices,
        export_limit_w=draw(st.one_of(st.none(), st.floats(0, 15_000))),
        # Review D2: flat mode (no prices, or spot prices without a formula) is generated too.
        with_prices=draw(st.sampled_from([True, True, True, False])),
        price_includes=draw(st.sampled_from(["all_in", "all_in", "all_in", "spot_only"])),
        import_limit_w=draw(st.one_of(st.none(), st.floats(2_000, 30_000))),
    )
    return replace(situation)
