# SPDX-License-Identifier: Apache-2.0
"""Plan snapshots at a time, their comparison and the "Why?" of a decision (Phase 5 WP4)."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Final, Literal

from suncore.api.history_models import (
    DecisionEntryOut,
    DecisionExplainOut,
    ExplainInputOut,
    ExplainPlanOut,
    IntervalDiffOut,
    PlanAtOut,
    PlanCompareOut,
    SnapshotRefOut,
)
from suncore.api.history_queries import (
    ERR_HISTORY_RANGE_INVALID,
    ERR_HISTORY_UNAVAILABLE,
    fail,
    local,
)
from suncore.api.services import HistorySources
from suncore.core.control import DEFAULT_BAND, Plan, PlanInterval, plan_from_payload
from suncore.core.measurements import (
    SITE_BATTERY_W,
    SITE_GRID_W,
    SITE_HOUSE_W,
    SITE_PV_W,
    SITE_SOC_PCT,
    device_on_key,
    device_power_key,
)
from suncore.core.series import TimeRange
from suncore.storage.plans import PlanSnapshot, PlanSnapshotStore
from suncore.storage.timeseries import MINUTE_KEEP

ERR_SNAPSHOT_NOT_FOUND: Final = "api.error.snapshot_not_found"
ERR_DECISION_NOT_FOUND: Final = "api.error.decision_not_found"
NEIGHBOURS: Final = 3
EPS: Final = 1e-6
_ID_RE: Final = re.compile(r"^\d{1,12}$")


def _plans(sources: HistorySources) -> PlanSnapshotStore:
    if sources.plans is None:
        raise fail(503, ERR_HISTORY_UNAVAILABLE)
    return sources.plans


def _plan_of(snapshot: PlanSnapshot) -> Plan:
    try:
        return plan_from_payload(snapshot.payload)
    except ValueError as exc:  # stored by a newer version or damaged: same as pruned
        raise fail(404, ERR_SNAPSHOT_NOT_FOUND) from exc


def interval_index(plan: Plan, at: datetime) -> int | None:
    for i, interval in enumerate(plan.intervals):
        if interval.covers(at):
            return i
    return None


async def plan_at(sources: HistorySources, moment: datetime) -> PlanAtOut:
    moment = local(moment, sources.context.tz)
    snapshot = await _plans(sources).at(moment)
    if snapshot is None:
        raise fail(404, ERR_SNAPSHOT_NOT_FOUND)
    plan = _plan_of(snapshot)
    return PlanAtOut(
        snapshot_id=snapshot.id,
        created_at=snapshot.created_at,
        source=snapshot.source,
        schema_version=snapshot.schema_version,
        at=moment,
        interval_index=interval_index(plan, moment),
        plan=plan,
    )


async def _resolve(sources: HistorySources, ref: str) -> tuple[PlanSnapshot, Plan]:
    """``ref`` is a snapshot id (digits) or a time (the snapshot valid then)."""
    plans = _plans(sources)
    if _ID_RE.match(ref):
        snapshot = await plans.get(int(ref))
    else:
        try:
            moment = datetime.fromisoformat(ref.replace("Z", "+00:00"))
        except ValueError as exc:
            raise fail(422, ERR_HISTORY_RANGE_INVALID) from exc
        snapshot = await plans.at(local(moment, sources.context.tz))
    if snapshot is None:
        raise fail(404, ERR_SNAPSHOT_NOT_FOUND)
    return snapshot, _plan_of(snapshot)


def _delta(a: float | None, b: float | None) -> float | None:
    return None if a is None or b is None else b - a


def _get(obj: object | None, name: str) -> float | None:
    return None if obj is None else getattr(obj, name)


def _diff(a: PlanInterval | None, b: PlanInterval | None) -> IntervalDiffOut | None:
    ref = a or b
    assert ref is not None
    if a is None or b is None:
        return IntervalDiffOut(
            start=ref.start,
            end=ref.end,
            only_in="b" if a is None else "a",
            a=a,
            b=b,
            battery_w_delta=None,
            soc_pct_delta=None,
            import_w_delta=None,
            export_w_delta=None,
            devices_w_delta=None,
            import_price_delta=None,
            devices_changed=[],
        )
    ea, eb = a.expected, b.expected
    deltas = {
        "battery_w_delta": _delta(_get(ea, "battery_w"), _get(eb, "battery_w")),
        "soc_pct_delta": _delta(_get(ea, "soc_pct"), _get(eb, "soc_pct")),
        "import_w_delta": _delta(_get(ea, "import_w"), _get(eb, "import_w")),
        "export_w_delta": _delta(_get(ea, "export_w"), _get(eb, "export_w")),
        "devices_w_delta": _delta(_get(ea, "devices_w"), _get(eb, "devices_w")),
        "import_price_delta": _delta(
            _get(a.prices, "import_price"), _get(b.prices, "import_price")
        ),
    }
    changed = sorted(
        d
        for d in set(a.devices) | set(b.devices)
        if a.devices.get(d, DEFAULT_BAND) != b.devices.get(d, DEFAULT_BAND)
    )
    differs = (
        bool(changed)
        or a.battery != b.battery
        or any(v is not None and abs(v) > EPS for v in deltas.values())
    )
    if not differs:
        return None
    return IntervalDiffOut(
        start=ref.start, end=ref.end, only_in=None, a=a, b=b, devices_changed=changed, **deltas
    )


def _ref(snapshot: PlanSnapshot, plan: Plan) -> SnapshotRefOut:
    return SnapshotRefOut(
        snapshot_id=snapshot.id,
        created_at=snapshot.created_at,
        source=snapshot.source,
        intervals=len(plan.intervals),
    )


async def plan_compare(sources: HistorySources, a: str, b: str) -> PlanCompareOut:
    snap_a, plan_a = await _resolve(sources, a)
    snap_b, plan_b = await _resolve(sources, b)
    by_a = {i.start: i for i in plan_a.intervals}
    by_b = {i.start: i for i in plan_b.intervals}
    diffs = [
        d
        for start in sorted(set(by_a) | set(by_b))
        if (d := _diff(by_a.get(start), by_b.get(start))) is not None
    ]
    return PlanCompareOut(
        a=_ref(snap_a, plan_a), b=_ref(snap_b, plan_b), changed=len(diffs), intervals=diffs
    )


async def _inputs(
    sources: HistorySources, at: datetime, asset_id: str
) -> tuple[list[ExplainInputOut], datetime | None, str | None]:
    store = sources.timeseries
    if store is None:
        return [], None, None
    fine_ok = at >= sources.clock() - MINUTE_KEEP
    keys = [
        (SITE_GRID_W, "W"),
        (SITE_PV_W, "W"),
        (SITE_HOUSE_W, "W"),
        (SITE_BATTERY_W, "W"),
        (SITE_SOC_PCT, "%"),
        (device_power_key(asset_id), "W"),
        (device_on_key(asset_id), "share"),
    ]
    out: list[ExplainInputOut] = []
    used: set[str] = set()
    for key, unit in keys:
        value: float | None = None
        steps: tuple[tuple[Literal["1m", "15m"], int], ...] = (("1m", 60), ("15m", 900))
        for res, step_s in steps:
            if res == "1m" and not fine_ok:
                continue
            first = int(at.timestamp()) // step_s * step_s
            window = TimeRange(
                start=datetime.fromtimestamp(first, UTC),
                end=datetime.fromtimestamp(first + step_s, UTC),
            )
            read = await store.read_series(key, window, res)
            value = read.values[0] if read.values else None
            if value is not None:
                used.add(res)
                break
        out.append(ExplainInputOut(key=key, value=value, unit=unit))
    quarter = datetime.fromtimestamp(int(at.timestamp()) // 900 * 900, at.tzinfo)
    resolution = "1m" if "1m" in used else "15m"
    return out, quarter, resolution


async def explain(sources: HistorySources, decision_id: int) -> DecisionExplainOut:
    log = sources.decision_log
    decision = await log.get(decision_id)
    if decision is None:
        raise fail(404, ERR_DECISION_NOT_FOUND)
    before, after = await log.neighbours(decision_id, decision.intent.asset_id, NEIGHBOURS)
    plan_out: ExplainPlanOut | None = None
    if sources.plans is not None:
        snapshot = await sources.plans.at(decision.ts)
        if snapshot is not None:
            try:
                plan: Plan | None = plan_from_payload(snapshot.payload)
            except ValueError:
                plan = None
            if plan is not None:
                idx = interval_index(plan, decision.ts)
                plan_out = ExplainPlanOut(
                    snapshot_id=snapshot.id,
                    created_at=snapshot.created_at,
                    source=snapshot.source,
                    interval_index=idx,
                    interval=None if idx is None else plan.intervals[idx],
                )
    inputs, inputs_at, resolution = await _inputs(sources, decision.ts, decision.intent.asset_id)
    return DecisionExplainOut.model_validate(
        {
            "id": decision_id,
            "decision": decision,
            "before": [DecisionEntryOut(id=i, decision=d) for i, d in before],
            "after": [DecisionEntryOut(id=i, decision=d) for i, d in after],
            "plan": plan_out,
            "inputs": inputs,
            "inputs_at": inputs_at,
            "inputs_resolution": resolution,
        }
    )
