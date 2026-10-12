# SPDX-License-Identifier: Apache-2.0
"""Signal types: identifiers, quality, samples and snapshots."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from types import MappingProxyType
from typing import NewType

from suncore.core.units import Unit

SignalId = NewType("SignalId", str)
"""Identifier of a signal (a Home Assistant entity id or a role name)."""


class Quality(Enum):
    """Data quality, ordered best to worst: ok < stale < unavailable < invalid."""

    OK = "ok"
    STALE = "stale"
    UNAVAILABLE = "unavailable"
    INVALID = "invalid"

    @property
    def severity(self) -> int:
        return _ORDER.index(self)

    @staticmethod
    def worst(qualities: Iterable[Quality]) -> Quality:
        """Worst of the given qualities; an empty input yields ``OK``."""
        return max(qualities, key=lambda q: q.severity, default=Quality.OK)


_ORDER: tuple[Quality, ...] = (
    Quality.OK,
    Quality.STALE,
    Quality.UNAVAILABLE,
    Quality.INVALID,
)


def worst(*qualities: Quality) -> Quality:
    """Combine qualities, returning the worst one (``OK`` for no arguments)."""
    return Quality.worst(qualities)


@dataclass(frozen=True, slots=True)
class Sample:
    """One reading. ``ts`` must be timezone-aware and is normalised to UTC."""

    value: float | None
    unit: Unit | None
    ts: datetime
    quality: Quality = Quality.OK

    def __post_init__(self) -> None:
        if self.ts.tzinfo is None or self.ts.utcoffset() is None:
            msg = "Sample.ts must be timezone-aware"
            raise ValueError(msg)
        if self.ts.tzinfo is not UTC:
            object.__setattr__(self, "ts", self.ts.astimezone(UTC))


@dataclass(frozen=True, slots=True)
class SignalSnapshot:
    """Immutable mapping of role (or signal id) -> :class:`Sample` at a time."""

    ts: datetime
    samples: Mapping[str, Sample] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.ts.tzinfo is None or self.ts.utcoffset() is None:
            msg = "SignalSnapshot.ts must be timezone-aware"
            raise ValueError(msg)
        object.__setattr__(self, "ts", self.ts.astimezone(UTC))
        object.__setattr__(self, "samples", MappingProxyType(dict(self.samples)))

    def get(self, role: str) -> Sample | None:
        return self.samples.get(role)

    def __getitem__(self, role: str) -> Sample:
        return self.samples[role]

    def __contains__(self, role: object) -> bool:
        return role in self.samples

    def __len__(self) -> int:
        return len(self.samples)
