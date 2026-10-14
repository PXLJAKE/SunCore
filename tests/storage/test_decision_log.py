# SPDX-License-Identifier: Apache-2.0
"""DecisionLog and its use as the SafetyGate sink."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from suncore.core.decisions import (
    Decision,
    DecisionKind,
    Intent,
    OperatingMode,
    Origin,
    Reason,
    SwitchAction,
)
from suncore.safety.gate import DecisionSink, SafetyGate
from suncore.storage import Database, DecisionLog
from suncore.storage.decisions import MAX_LIMIT

T0 = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)


def make_intent(n: int = 0, asset: str = "device.a") -> Intent:
    return Intent(
        id=f"i-{n}",
        ts=T0 + timedelta(seconds=n),
        asset_id=asset,
        action=SwitchAction(on=True),
        reasons=(Reason(key="controller.surplus", args={"watts": 1200.5}),),
        origin=Origin.CONTROLLER,
    )


def make_decision(n: int = 0) -> Decision:
    return Decision(
        intent=make_intent(n),
        kind=DecisionKind.SHADOW,
        ts=T0 + timedelta(seconds=n),
        reasons=(Reason(key="safety.shadow_mode"),),
        mode=OperatingMode.SHADOW,
    )


async def test_append_and_latest_round_trip(db: Database) -> None:
    log = DecisionLog(db)
    for n in range(5):
        await log.append(make_decision(n))
    latest = await log.latest(3)
    assert latest == [make_decision(4), make_decision(3), make_decision(2)]
    assert await log.count() == 5
    row = await db.fetchone("SELECT asset_id, layer, kind, reasons FROM decision WHERE id = 1")
    assert row is not None
    assert row[:3] == ("device.a", "safety", "shadow")
    assert json.loads(str(row[3])) == [{"key": "safety.shadow_mode", "args": {}}]


async def test_latest_limit_is_bounded(db: Database) -> None:
    log = DecisionLog(db)
    await log.append(make_decision())
    assert len(await log.latest(0)) == 1
    assert len(await log.latest(-5)) == 1
    assert len(await log.latest(10**9)) == 1
    assert MAX_LIMIT <= 1000
    assert await DecisionLog(db).latest() == [make_decision()]


async def test_gate_writes_into_the_log(db: Database) -> None:
    log = DecisionLog(db)
    sink: DecisionSink = log.append
    gate = SafetyGate(clock=lambda: T0, sink=sink)
    d1 = await gate.submit(make_intent(1))
    d2 = await gate.submit(make_intent(2, asset="device.b"))
    assert await log.latest(10) == [d2, d1]
    assert all(d.kind is DecisionKind.SHADOW for d in await log.latest(10))


V1_ROW = (
    '{"intent": {"id": "i-9", "ts": "2026-10-12T12:00:09Z", "asset_id": "device.a", '
    '"action": {"kind": "switch", "on": true}, "reasons": [], "origin": "controller"}, '
    '"kind": "shadow", "ts": "2026-10-12T12:00:09Z", '
    '"reasons": [{"key": "safety.shadow_mode", "args": {}}], "mode": "shadow"}'
)
"""Payload exactly as the Phase 1 ``DecisionLog.append`` stored it (no ``schema_version``)."""


async def test_latest_loads_phase_1_rows_and_skips_corrupt_ones(db: Database) -> None:
    log = DecisionLog(db)
    await log.append(make_decision(1))
    await db.execute(
        "INSERT INTO decision (ts, asset_id, layer, kind, payload, reasons) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("2026-10-12T12:00:09+00:00", "device.a", "safety", "shadow", V1_ROW, "[]"),
    )
    await db.execute(
        "INSERT INTO decision (ts, asset_id, layer, kind, payload, reasons) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("2026-10-12T12:00:10+00:00", "device.a", "safety", "shadow", '{"kind": "shadow"}', "[]"),
    )
    await db.execute(
        "INSERT INTO decision (ts, asset_id, layer, kind, payload, reasons) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            "2026-10-12T12:00:11+00:00",
            "device.a",
            "safety",
            "shadow",
            V1_ROW.replace('"kind": "shadow"', '"kind": "shadow", "schema_version": 42'),
            "[]",
        ),
    )
    latest = await log.latest(10)
    assert [d.intent.id for d in latest] == ["i-9", "i-1"]
    assert latest[0].schema_version == 2
    assert latest[0].kind is DecisionKind.SHADOW
    assert await log.count() == 4
