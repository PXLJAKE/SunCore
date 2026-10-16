# SPDX-License-Identifier: Apache-2.0
"""Plan schema v2 (Phase 4 WP2): additive fields, v1 payloads keep loading."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from suncore.core.control import (
    PLAN_SCHEMA_VERSION,
    BatteryBand,
    IntervalFlows,
    IntervalPrices,
    Plan,
    PlanInterval,
    SolverStats,
    plan_from_payload,
)
from suncore.core.decisions import Reason

T0 = datetime(2026, 6, 1, tzinfo=UTC)
Q = timedelta(minutes=15)

V1_PAYLOAD = {
    "schema_version": 1,
    "created_at": "2026-06-01T00:00:00Z",
    "intervals": [
        {
            "start": "2026-06-01T00:00:00Z",
            "end": "2026-06-01T00:15:00Z",
            "devices": {"pool": {"allowed": False, "preference": "neutral", "max_power_w": None}},
            "battery": {
                "min_power_w": None,
                "max_power_w": None,
                "min_soc_pct": 15,
                "max_soc_pct": None,
                "charge_from_surplus": True,
            },
            "grid_import_limit_w": None,
        }
    ],
    "reasons": [],
    "assumptions": {"mode": "shadow"},
}


def test_new_plans_are_v2_and_default_is_default() -> None:
    assert PLAN_SCHEMA_VERSION == 2
    p = Plan.default()
    assert (p.schema_version, p.source, p.stats, p.is_default) == (2, "default", None, True)


def test_v1_payload_loads_unchanged_and_through_the_loader() -> None:
    plain = Plan.model_validate(V1_PAYLOAD)
    assert plain.schema_version == 1
    assert plain.source == "default"
    assert plain.intervals[0].expected is None
    assert plain.intervals[0].reasons == ()
    assert plain.intervals[0].battery.priority_until_soc_pct is None
    stamped = plan_from_payload(V1_PAYLOAD)
    assert stamped.schema_version == 2
    assert stamped.band_for("pool", T0).allowed is False
    assert plan_from_payload(json.loads(stamped.model_dump_json())) == stamped


@pytest.mark.parametrize("version", [3, 0, "2", True])
def test_loader_refuses_unknown_versions(version: object) -> None:
    with pytest.raises(ValueError, match="schema_version"):
        plan_from_payload({**V1_PAYLOAD, "schema_version": version})


def test_v2_interval_round_trip() -> None:
    flows = IntervalFlows(
        pv_p10_w=800,
        pv_p50_w=1200,
        pv_p90_w=1500,
        load_w=600,
        import_w=0,
        export_w=100,
        battery_w=500,
        soc_pct=42.5,
    )
    interval = PlanInterval(
        start=T0,
        end=T0 + Q,
        battery=BatteryBand(min_soc_pct=15, priority_until_soc_pct=60),
        expected=flows,
        prices=IntervalPrices(import_price=0.31, export_price=-0.01),
        reasons=(Reason(key="planner.battery.charge_from_pv", args={"pv_p10_kwh": 0.2}),),
    )
    plan = Plan(
        created_at=T0,
        intervals=(interval,),
        source="solver",
        stats=SolverStats(
            solve_time_s=0.8, status="optimal", objective=1.23, n_vars=960, n_binaries=0, mip_gap=0
        ),
    )
    assert Plan.model_validate_json(plan.model_dump_json()) == plan
    assert plan.battery_band_at(T0).priority_until_soc_pct == 60


@pytest.mark.parametrize(
    "bad",
    [
        {"pv_p10_w": 1300},  # above p50
        {"pv_p90_w": 1100},  # below p50
        {"import_w": -1},
        {"soc_pct": 101},
        {"load_w": float("nan")},
    ],
)
def test_interval_flows_reject(bad: dict[str, float]) -> None:
    doc = {"pv_p50_w": 1200, "load_w": 600, "import_w": 0, "export_w": 0} | bad
    with pytest.raises(ValidationError):
        IntervalFlows.model_validate(doc)


def test_plan_source_stats_and_band_validation() -> None:
    with pytest.raises(ValidationError):
        Plan.model_validate({"source": "magic"})
    with pytest.raises(ValidationError):
        SolverStats(solve_time_s=-1, status="optimal")
    with pytest.raises(ValidationError):
        SolverStats(solve_time_s=1, status="Not A Slug")
    with pytest.raises(ValidationError):
        BatteryBand(priority_until_soc_pct=120)
    assert IntervalFlows(pv_p50_w=0, load_w=0, import_w=0, export_w=0).battery_w == 0
