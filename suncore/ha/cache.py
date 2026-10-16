# SPDX-License-Identifier: Apache-2.0
"""Entity cache with per-entity staleness and conversion to ``core.signals.Sample``.

Quality rules (``docs/plans/phase-1.md`` WP3, ``core/balance`` contract):

* state ``unavailable`` / ``unknown`` -> ``Quality.UNAVAILABLE``, no value;
* a state that is not a finite decimal number -> ``Quality.INVALID``, no value;
* otherwise ``OK``, or ``STALE`` (value kept) when the entity was not refreshed within
  its maximum age. The worst of the applicable qualities wins.
* the unit comes from ``unit_of_measurement`` via ``core.units.parse_unit``; an unknown
  unit yields ``unit=None`` and ``core.balance`` treats that as invalid.

Freshness. Home Assistant only emits ``state_changed`` when the state or attributes
change; a sensor that keeps reporting the same value (0 W at night) is silent on the
``subscribe_entities`` stream. Therefore:

* every stream event for an entity refreshes it;
* a *full* update (initial snapshot, resubscribe, periodic ``get_states`` resync)
  refreshes an entity only if it is new or its state, attributes or ``last_reported``
  differ from the cached copy. ``last_reported`` (``get_states``) advances whenever the
  integration reports, even with an unchanged value, and is compared only for
  inequality, so the server clock never matters.

Many integrations only write a state when the value changes (seen live on 2026-10-16:
the SunSpec battery state of charge and the ``smartthinq_sensors`` power sensors keep
``last_reported`` unchanged for hours). Silence is then normal, not a fault. With
:meth:`EntityCache.set_heartbeats` the cache judges the age only for the given
*heartbeat* entities (the grid meter, which changes with every load step); every other
available entity keeps quality ``OK`` however long its value stays the same. Without
heartbeats (the default) every entity is judged by its age.

Verified 2026-10-12 against ``home-assistant/core`` ``core.py``: ``State.last_reported``,
``EVENT_STATE_REPORTED`` is a separate event that ``state_changed`` subscriptions do not
carry; ``State._as_dict`` includes ``last_reported``.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from suncore.core.signals import Quality, Sample
from suncore.core.units import parse_unit
from suncore.ha.connection import EntityState, EntityUpdate, HAConnection, Unsubscribe

DEFAULT_MAX_AGE_S: Final = 120.0
UNAVAILABLE_STATES: Final = frozenset({"unavailable", "unknown"})
_NUMBER = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")


def utc_now() -> datetime:
    """Default wall clock of the cache (replaced by a fake in tests)."""
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class CachedEntity:
    state: EntityState
    refreshed_at: datetime
    """When SunCore last saw proof that the entity is alive (see module docstring)."""


def parse_numeric(text: str) -> float | None:
    """Parse a HA numeric state; ``None`` for anything else (including nan/inf)."""
    candidate = text.strip()
    if not _NUMBER.fullmatch(candidate):
        return None
    value = float(candidate)
    return value if math.isfinite(value) else None


class EntityCache:
    """Current state of every subscribed entity."""

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] = utc_now,
        max_age_s: float = DEFAULT_MAX_AGE_S,
        max_age_overrides: Mapping[str, float] | None = None,
    ) -> None:
        if max_age_s <= 0:
            msg = "max_age_s must be positive"
            raise ValueError(msg)
        self._clock = clock
        self._max_age_s = max_age_s
        self._overrides = dict(max_age_overrides or {})
        self._entries: dict[str, CachedEntity] = {}
        self._heartbeats: frozenset[str] | None = None

    # --- feeding ----------------------------------------------------------

    def attach(self, connection: HAConnection) -> Unsubscribe:
        """Feed the cache from a connection's entity updates."""
        return connection.add_entity_listener(self.ingest)

    def ingest(self, update: EntityUpdate) -> None:
        now = self._now()
        if update.full:
            for gone in set(self._entries) - set(update.changed):
                del self._entries[gone]
            for entity_id, state in update.changed.items():
                previous = self._entries.get(entity_id)
                if previous is not None and _same_content(previous.state, state):
                    self._entries[entity_id] = CachedEntity(state, previous.refreshed_at)
                else:
                    self._entries[entity_id] = CachedEntity(state, now)
        else:
            for entity_id in update.removed:
                self._entries.pop(entity_id, None)
            for entity_id, state in update.changed.items():
                self._entries[entity_id] = CachedEntity(state, now)

    def ingest_states(self, states: Iterable[EntityState]) -> None:
        """Apply a ``get_states`` result as a full snapshot."""
        self.ingest(EntityUpdate(changed={s.entity_id: s for s in states}, full=True))

    # --- reading ----------------------------------------------------------

    def get(self, entity_id: str) -> CachedEntity | None:
        return self._entries.get(entity_id)

    def ids(self) -> list[str]:
        """Entity ids currently held (insertion order)."""
        return list(self._entries)

    def __contains__(self, entity_id: object) -> bool:
        return entity_id in self._entries

    def __len__(self) -> int:
        return len(self._entries)

    def set_max_age_overrides(self, overrides: Mapping[str, float]) -> None:
        """Replace the per-entity maximum ages (slow sensors; the composition root's cadences)."""
        if any(age <= 0 for age in overrides.values()):
            msg = "maximum ages must be positive"
            raise ValueError(msg)
        self._overrides = dict(overrides)

    def set_heartbeats(self, entity_ids: Iterable[str] | None) -> None:
        """Judge staleness only for these entities (``None``: for every entity)."""
        self._heartbeats = None if entity_ids is None else frozenset(entity_ids)

    def judges_age(self, entity_id: str) -> bool:
        """Whether a silent ``entity_id`` turns ``STALE`` (see :meth:`set_heartbeats`)."""
        return self._heartbeats is None or entity_id in self._heartbeats

    def max_age_s(self, entity_id: str) -> float:
        return self._overrides.get(entity_id, self._max_age_s)

    def age_s(self, entity_id: str) -> float | None:
        entry = self._entries.get(entity_id)
        return None if entry is None else (self._now() - entry.refreshed_at).total_seconds()

    def is_stale(self, entity_id: str) -> bool:
        """``True`` if unknown to the cache, or silent beyond its maximum age (heartbeats only)."""
        age = self.age_s(entity_id)
        if age is None:
            return True
        return self.judges_age(entity_id) and age > self.max_age_s(entity_id)

    def sample(self, entity_id: str) -> Sample | None:
        entry = self._entries.get(entity_id)
        if entry is None:
            return None
        return self._to_sample(entry, self._now())

    def samples(self, entity_ids: Iterable[str] | None = None) -> dict[str, Sample]:
        """Samples by entity id (for ``core.balance.compute_flows``).

        Entities the cache has never seen (or that were removed) are omitted;
        ``compute_flows`` reports those as unavailable.
        """
        now = self._now()
        ids = self._entries.keys() if entity_ids is None else entity_ids
        return {
            eid: self._to_sample(self._entries[eid], now) for eid in ids if eid in self._entries
        }

    # --- internals --------------------------------------------------------

    def _now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            msg = "cache clock must return timezone-aware datetimes"
            raise ValueError(msg)
        return now

    def _to_sample(self, entry: CachedEntity, now: datetime) -> Sample:
        state = entry.state
        unit_text = state.attributes.get("unit_of_measurement")
        unit = parse_unit(unit_text if isinstance(unit_text, str) else None)
        ts = entry.refreshed_at
        if state.state in UNAVAILABLE_STATES:
            return Sample(None, unit, ts, Quality.UNAVAILABLE)
        value = parse_numeric(state.state)
        if value is None:
            return Sample(None, unit, ts, Quality.INVALID)
        stale = self.judges_age(state.entity_id) and (
            now - entry.refreshed_at
        ).total_seconds() > self.max_age_s(state.entity_id)
        return Sample(value, unit, ts, Quality.STALE if stale else Quality.OK)


def _same_content(a: EntityState, b: EntityState) -> bool:
    return (
        a.state == b.state and a.attributes == b.attributes and a.last_reported == b.last_reported
    )
