# SPDX-License-Identifier: Apache-2.0
"""Load increases are counted per ``GateInputs`` snapshot (review 3-B, G1).

Every intent of one tick is judged against the same snapshot, whose free surplus does not
show the increases the gate has just let through. The gate therefore claims each accepted
increase for the snapshot's ``ts`` and subtracts the claims from the surplus for the next
intents; a rejected/failed write returns its claim, a new snapshot starts from zero.
"""

from __future__ import annotations

import asyncio

from hypothesis import given, settings
from hypothesis import strategies as st

from suncore.core.control import GateInputs
from suncore.core.decisions import Decision, DecisionKind, OperatingMode, SwitchAction
from tests.safety.helpers import flows, make_intent, surplus, switch_device
from tests.safety.test_gate import Harness, make_harness

ASSISTED = OperatingMode.ASSISTED


async def _yield(_: float) -> None:
    await asyncio.sleep(0)  # a real suspension point: the submits interleave


def harness(nominals: list[float], free_w: float, unc_w: float = 0.0, **kw: object) -> Harness:
    devices = tuple(switch_device(f"d{i}", nominal_w=n) for i, n in enumerate(nominals))
    inputs = GateInputs(flows=flows(), surplus=surplus(free_w, unc_w))
    return make_harness(devices, inputs=inputs, sleep=_yield, **kw)  # type: ignore[arg-type]


def on(device_id: str, n: int = 1) -> object:
    return make_intent(SwitchAction(on=True), asset_id=device_id, intent_id=f"{device_id}-{n}")


async def gather(h: Harness, *device_ids: str) -> list[Decision]:
    return list(await asyncio.gather(*(h.gate.submit(on(d)) for d in device_ids)))  # type: ignore[arg-type]


def test_two_concurrent_rises_cannot_share_one_surplus() -> None:
    h = harness([700.0, 700.0], 1000.0)
    a, b = asyncio.run(gather(h, "d0", "d1"))
    assert a.kind is DecisionKind.ACCEPTED
    assert b.kind is DecisionKind.REJECTED
    assert [r.key for r in b.reasons] == ["safety.grid_import_caused"]
    assert b.reasons[0].args == {"need_w": 700, "surplus_w": 300}
    assert h.apply_calls == 1


def test_a_new_snapshot_starts_from_zero() -> None:
    h = harness([700.0, 700.0], 1000.0)
    asyncio.run(gather(h, "d0"))
    h.clock.advance(5)  # the controller's next tick: a new GateInputs.ts
    (b,) = asyncio.run(gather(h, "d1"))
    assert b.kind is DecisionKind.ACCEPTED


def test_a_failed_write_returns_its_claim() -> None:
    h = harness([700.0, 700.0], 1000.0)
    h.actuators["d0"].fail_apply = RuntimeError("boom")
    (a,) = asyncio.run(gather(h, "d0"))
    assert a.kind is DecisionKind.FAILED
    (b,) = asyncio.run(gather(h, "d1"))
    assert b.kind is DecisionKind.ACCEPTED


def test_fitting_rises_all_pass() -> None:
    h = harness([300.0, 300.0, 300.0], 1000.0)
    assert [d.kind for d in asyncio.run(gather(h, "d0", "d1", "d2"))] == [DecisionKind.ACCEPTED] * 3


def test_confirmed_proposals_are_judged_with_the_claims_of_the_current_snapshot() -> None:
    h = harness([700.0, 700.0], 1000.0, device_mode=ASSISTED, global_mode=ASSISTED)
    proposals = asyncio.run(gather(h, "d0", "d1"))
    assert [p.kind for p in proposals] == [DecisionKind.PROPOSED] * 2  # proposals claim nothing

    async def confirm_both() -> list[Decision | None]:
        return list(await asyncio.gather(*(h.gate.confirm(p.intent.id) for p in proposals)))

    first, second = asyncio.run(confirm_both())
    assert first is not None
    assert second is not None
    assert first.kind is DecisionKind.ACCEPTED
    assert second.kind is DecisionKind.REJECTED
    assert second.reasons[0].key == "safety.grid_import_caused"


@settings(max_examples=150, deadline=None)
@given(
    nominals=st.lists(st.integers(min_value=10, max_value=3000), min_size=1, max_size=6),
    free=st.floats(min_value=0, max_value=8000),
    unc=st.floats(min_value=0, max_value=2000),
)
def test_accepted_increases_never_exceed_the_snapshot_surplus(
    nominals: list[int], free: float, unc: float
) -> None:
    h = harness([float(n) for n in nominals], free, unc)
    lower = surplus(free, unc).lower_w
    decisions = asyncio.run(gather(h, *(f"d{i}" for i in range(len(nominals)))))
    accepted = sum(nominals[i] for i, d in enumerate(decisions) if d.kind is DecisionKind.ACCEPTED)
    assert accepted <= lower + 1e-6
    # Greedy in submission order: a rejected one really did not fit any more.
    used = 0.0
    for i, d in enumerate(decisions):
        if d.kind is DecisionKind.ACCEPTED:
            used += nominals[i]
        else:
            assert d.kind is DecisionKind.REJECTED
            assert used + nominals[i] > lower - 1e-6
