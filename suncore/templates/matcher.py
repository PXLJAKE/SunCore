# SPDX-License-Identifier: Apache-2.0
"""Role matching: which entities fit a template role, and how well.

All criteria given by the matcher must hold; an unknown attribute of the entity
(``None``) never matches a given criterion. The score only ranks entities that match,
so rarer criteria weigh more (same device, translation key and platform over device
class and unit).

``same_device_id`` implements ``RoleSpec.same_device_as``: the caller passes the registry
device of the entity already chosen for the referenced role. A candidate of another
known device is dropped; a candidate without known device stays, unboosted.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Final

from suncore.core.entities import EntityMeta
from suncore.templates.models import EntityMatcher, compile_pattern

WEIGHTS: Final[dict[str, int]] = {
    "domain": 10,
    "state_class": 10,
    "units": 20,
    "unit_pattern": 20,
    "device_class": 30,
    "platforms": 40,
    "translation_key": 50,
    "translation_key_pattern": 50,
    "same_device": 60,
}


def _fullmatch(pattern: str | None, value: str | None) -> bool:
    return (
        value is not None
        and pattern is not None
        and (compile_pattern(pattern).fullmatch(value) is not None)
    )


def _checks(matcher: EntityMatcher, meta: EntityMeta) -> list[tuple[str, bool]]:
    """``(criterion, satisfied)`` for every criterion the matcher gives."""
    unit = None if meta.unit is None else meta.unit.strip()
    checks: list[tuple[str, bool]] = []
    for name in ("device_class", "translation_key", "state_class"):
        wanted: str | None = getattr(matcher, name)
        if wanted is not None:
            checks.append((name, getattr(meta, name) == wanted))
    if matcher.platforms is not None:
        checks.append(("platforms", meta.platform in matcher.platforms))
    if matcher.translation_key_pattern is not None:
        checks.append(
            (
                "translation_key_pattern",
                _fullmatch(matcher.translation_key_pattern, meta.translation_key),
            )
        )
    if matcher.units is not None:
        checks.append(("units", unit is not None and unit in matcher.units))
    if matcher.unit_pattern is not None:
        checks.append(("unit_pattern", _fullmatch(matcher.unit_pattern, unit)))
    return checks


def matched_criteria(
    matcher: EntityMatcher, meta: EntityMeta, *, same_device_id: str | None = None
) -> tuple[str, ...] | None:
    """Names of the criteria the entity satisfies, or ``None`` if any given one fails."""
    if meta.domain != matcher.domain:
        return None
    checks = _checks(matcher, meta)
    if any(not ok for _, ok in checks):
        return None
    hits = ["domain", *(name for name, _ in checks)]
    if same_device_id is not None and meta.device_id is not None:
        if meta.device_id != same_device_id:
            return None
        hits.append("same_device")
    return tuple(hits)


def match_role(
    matcher: EntityMatcher, meta: EntityMeta, *, same_device_id: str | None = None
) -> int | None:
    """Score of ``meta`` for ``matcher`` (higher = more specific), ``None`` if it does not match."""
    hits = matched_criteria(matcher, meta, same_device_id=same_device_id)
    if hits is None:
        return None
    return sum(WEIGHTS[h] for h in hits)


def rank_entities(
    matcher: EntityMatcher,
    entities: Iterable[EntityMeta],
    *,
    same_device_id: str | None = None,
) -> list[tuple[EntityMeta, int]]:
    """Matching entities, best score first, ties by entity id."""
    scored = [
        (m, s) for m in entities if (s := match_role(matcher, m, same_device_id=same_device_id))
    ]
    scored.sort(key=lambda item: (-item[1], item[0].entity_id))
    return scored
