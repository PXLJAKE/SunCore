# SPDX-License-Identifier: Apache-2.0
"""Role matching: which entities fit a template role, and how well.

All criteria given by the matcher must hold; an unknown attribute of the entity
(``None``) never matches a given criterion. The score only ranks entities that match,
so rarer criteria weigh more (translation key and platform over device class and unit).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Final

from suncore.core.entities import EntityMeta
from suncore.devices.templates.models import EntityMatcher

WEIGHTS: Final[dict[str, int]] = {
    "domain": 10,
    "state_class": 10,
    "units": 20,
    "device_class": 30,
    "platform": 40,
    "translation_key": 50,
}


def matched_criteria(matcher: EntityMatcher, meta: EntityMeta) -> tuple[str, ...] | None:
    """Names of the criteria the entity satisfies, or ``None`` if any given one fails."""
    if meta.domain != matcher.domain:
        return None
    hits = ["domain"]
    for name in ("device_class", "platform", "translation_key", "state_class"):
        wanted: str | None = getattr(matcher, name)
        if wanted is None:
            continue
        if getattr(meta, name) != wanted:
            return None
        hits.append(name)
    if matcher.units is not None:
        if meta.unit is None or meta.unit.strip() not in matcher.units:
            return None
        hits.append("units")
    return tuple(hits)


def match_role(matcher: EntityMatcher, meta: EntityMeta) -> int | None:
    """Score of ``meta`` for ``matcher`` (higher = more specific), ``None`` if it does not match."""
    hits = matched_criteria(matcher, meta)
    if hits is None:
        return None
    return sum(WEIGHTS[h] for h in hits)


def rank_entities(
    matcher: EntityMatcher, entities: Iterable[EntityMeta]
) -> list[tuple[EntityMeta, int]]:
    """Matching entities, best score first, ties by entity id."""
    scored = [(m, s) for m in entities if (s := match_role(matcher, m)) is not None]
    scored.sort(key=lambda item: (-item[1], item[0].entity_id))
    return scored
