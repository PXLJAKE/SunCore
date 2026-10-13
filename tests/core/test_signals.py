# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone

import pytest
from hypothesis import given
from hypothesis import strategies as st

from suncore.core.signals import Quality, Sample, SignalId, SignalSnapshot, worst
from suncore.core.units import Unit

NOW = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)


def test_worst_order() -> None:
    assert worst() is Quality.OK
    assert worst(Quality.OK, Quality.STALE) is Quality.STALE
    assert worst(Quality.STALE, Quality.UNAVAILABLE) is Quality.UNAVAILABLE
    assert worst(Quality.UNAVAILABLE, Quality.INVALID, Quality.OK) is Quality.INVALID


@given(st.lists(st.sampled_from(list(Quality)), min_size=1))
def test_worst_is_max_and_commutative(qs: list[Quality]) -> None:
    result = worst(*qs)
    assert result in qs
    assert all(result.severity >= q.severity for q in qs)
    assert worst(*reversed(qs)) is result


def test_sample_requires_tz() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        Sample(1.0, Unit.W, datetime(2026, 1, 1))  # noqa: DTZ001


def test_sample_normalises_to_utc_and_is_frozen() -> None:
    plus2 = timezone(timedelta(hours=2))
    s = Sample(1.0, Unit.W, datetime(2026, 1, 1, 14, 0, tzinfo=plus2))
    assert s.ts == datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    assert s.ts.utcoffset() == timedelta(0)
    assert s.quality is Quality.OK
    with pytest.raises(FrozenInstanceError):
        s.value = 2.0  # type: ignore[misc]


def test_snapshot_mapping() -> None:
    s = Sample(5.0, Unit.W, NOW)
    src = {"pv": s}
    snap = SignalSnapshot(NOW, src)
    src["extra"] = s  # later mutation of the source must not leak in
    assert len(snap) == 1
    assert "pv" in snap
    assert "extra" not in snap
    assert snap["pv"] is s
    assert snap.get("nope") is None
    with pytest.raises(TypeError):
        snap.samples["x"] = s  # type: ignore[index]


def test_snapshot_requires_tz() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        SignalSnapshot(datetime(2026, 1, 1))  # noqa: DTZ001


def test_signal_id_is_str() -> None:
    assert SignalId("grid") == "grid"
