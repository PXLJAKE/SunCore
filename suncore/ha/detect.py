# SPDX-License-Identifier: Apache-2.0
"""Entity detection: which entities fit a site role or a template role, ranked, with reasons.

Site roles (grid, PV, battery, house, §14a signal) are defined here as
:class:`~suncore.templates.EntityMatcher` lists; template roles come from the template. Both go
through the same matcher (``rank_entities`` / ``matched_criteria``), so the criteria shown to
the user are exactly the ones that decided the ranking:

``detect.criterion.<name>`` with ``<name>`` one of ``domain``, ``device_class``, ``units``,
``unit_pattern``, ``platforms``, ``translation_key``, ``translation_key_pattern``,
``state_class``, ``same_device`` (matcher criteria), ``name`` (a role keyword occurs in the
entity name or id; a weak tie-breaker only) and ``name_affinity`` (template roles only: the
candidate's device name or friendly name contains the name of the device the user already
mapped, see :func:`detect_template_role`).

Entities that are disabled, hidden or of entity category ``config``/``diagnostic`` never
appear: a diagnostic "grid power" mirror or a hidden duplicate is not what a user wants to
map. Without registry data (R1) nothing is excluded, as in Phase 1.

The Phase 1 picker rules stay available as :func:`legacy_entities` (all power and battery
sensors, used when no role is given).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Final

from suncore.core.detection import DetectedEntity
from suncore.core.entities import EntityMeta
from suncore.core.signals import Quality
from suncore.ha.cache import UNAVAILABLE_STATES, EntityCache
from suncore.ha.candidates import entity_candidates
from suncore.ha.registry import EntityCatalogue
from suncore.templates import EntityMatcher, RoleSpec, matched_criteria, rank_entities
from suncore.templates.matcher import WEIGHTS

NAME_WEIGHT: Final = 15
NAME_AFFINITY_WEIGHT: Final = 25
"""Bonus for a candidate that shares the name of the device the user already mapped (below a
device class match, above a unit match); it only reorders candidates that match anyway."""
HIDDEN_MALUS: Final = 100
"""Subtracted from the score of hidden, diagnostic and config entities when they are included
(``include_hidden``): they rank below any proper candidate but stay selectable."""

CRITERION_KEYS: Final[Mapping[str, str]] = {
    "domain": "detect.criterion.domain",
    "device_class": "detect.criterion.device_class",
    "units": "detect.criterion.units",
    "unit_pattern": "detect.criterion.unit_pattern",
    "platforms": "detect.criterion.platforms",
    "translation_key": "detect.criterion.translation_key",
    "translation_key_pattern": "detect.criterion.translation_key_pattern",
    "state_class": "detect.criterion.state_class",
    "same_device": "detect.criterion.same_device",
    "name": "detect.criterion.name",
    "name_affinity": "detect.criterion.name_affinity",
}
"""Matcher criterion name to catalogue key; the frontend renders the keys."""

_POWER_UNITS: Final = ["W", "kW"]
_ENERGY_UNITS: Final = ["Wh", "kWh"]


@dataclass(frozen=True, slots=True)
class SiteRole:
    """A role of the site configuration and how to find its entities."""

    key: str
    matchers: tuple[EntityMatcher, ...]
    """Alternatives; the best score wins."""
    keywords: tuple[str, ...] = ()
    """Lower-case fragments that make a name more likely (substring match)."""
    require_keyword: bool = False
    """Only entities whose name contains a keyword are offered (very generic matchers)."""
    unclassified_ok: tuple[bool, ...] = ()
    """Per matcher: the matcher only applies to entities without a ``device_class``."""


def _role(
    key: str,
    *specs: tuple[EntityMatcher, bool],
    keywords: tuple[str, ...] = (),
    require_keyword: bool = False,
) -> SiteRole:
    return SiteRole(
        key,
        tuple(m for m, _ in specs),
        keywords,
        require_keyword,
        tuple(only for _, only in specs),
    )


def _power() -> tuple[tuple[EntityMatcher, bool], tuple[EntityMatcher, bool]]:
    return (
        (EntityMatcher(domain="sensor", device_class="power", units=_POWER_UNITS), False),
        # Template sensors often lack a device class; the unit decides (Phase 1 rule).
        (EntityMatcher(domain="sensor", units=_POWER_UNITS), True),
    )


def _energy() -> tuple[tuple[EntityMatcher, bool], tuple[EntityMatcher, bool]]:
    return (
        (EntityMatcher(domain="sensor", device_class="energy", units=_ENERGY_UNITS), False),
        (EntityMatcher(domain="sensor", units=_ENERGY_UNITS), True),
    )


SITE_ROLES: Final[Mapping[str, SiteRole]] = {
    r.key: r
    for r in (
        _role(
            "grid_power",
            *_power(),
            keywords=("grid", "netz", "meter", "zähler", "zaehler", "bezug"),
        ),
        _role(
            "grid_import_power",
            *_power(),
            keywords=("import", "bezug", "from_grid", "consum", "purchase"),
        ),
        _role(
            "grid_export_power",
            *_power(),
            keywords=("export", "einspeis", "feed", "to_grid", "return"),
        ),
        _role(
            "grid_energy_import",
            *_energy(),
            keywords=("import", "bezug", "from_grid", "consum", "purchase"),
        ),
        _role(
            "grid_energy_export",
            *_energy(),
            keywords=("export", "einspeis", "feed", "to_grid", "return"),
        ),
        _role(
            "pv_power",
            *_power(),
            keywords=(
                "pv",
                "solar",
                "photovolt",
                "inverter",
                "wechselrichter",
                "generat",
                "production",
                "erzeug",
            ),
        ),
        _role(
            "battery_power",
            *_power(),
            keywords=("batter", "akku", "storage", "speicher"),
        ),
        _role(
            "battery_soc",
            (EntityMatcher(domain="sensor", device_class="battery", units=["%"]), False),
            (EntityMatcher(domain="sensor", units=["%"]), True),
            keywords=("soc", "charge", "batter", "akku", "speicher", "ladestand"),
        ),
        _role(
            "house_power",
            *_power(),
            keywords=("house", "haus", "home", "load", "consum", "verbrauch"),
        ),
        _role(
            "price",
            (EntityMatcher(domain="sensor", unit_pattern=r"^[A-Z]{3}/kWh$"), False),
            keywords=("price", "preis", "tarif", "strompreis"),
        ),
        _role(
            "para14a_signal",
            (EntityMatcher(domain="binary_sensor"), False),
            (EntityMatcher(domain="input_boolean"), False),
            (EntityMatcher(domain="sensor"), False),
            keywords=("14a", "para14", "evu", "sperre", "rundsteuer", "netzbetreiber", "ripple"),
            require_keyword=True,
        ),
    )
}
"""All site roles by key (the ``?role=`` values of ``GET /api/v1/entities``)."""


@dataclass(frozen=True, slots=True)
class Match:
    """A ranked entity of a role."""

    meta: EntityMeta
    score: int
    criteria: tuple[str, ...]
    """Matcher criterion names (plus ``name``); see :data:`CRITERION_KEYS`."""


def is_hidden(meta: EntityMeta) -> bool:
    """Hidden, or a config/diagnostic entity (not listed on a default dashboard)."""
    return meta.hidden or meta.entity_category is not None


def is_candidate(meta: EntityMeta, *, include_hidden: bool = False) -> bool:
    """Exclude disabled entities, and hidden and config/diagnostic ones unless asked for."""
    if meta.disabled:
        return False
    return include_hidden or not is_hidden(meta)


def _keyword_hit(meta: EntityMeta, keywords: tuple[str, ...]) -> bool:
    haystack = f"{meta.name or ''} {meta.entity_id}".lower()
    haystack = re.sub(r"[\s\-]+", "_", haystack)
    return any(k in haystack for k in keywords)


def detect_site_role(
    role_key: str, metas: Iterable[EntityMeta], *, include_hidden: bool = False
) -> list[Match]:
    """Ranked matches for a site role; raises ``KeyError`` for an unknown role."""
    role = SITE_ROLES[role_key]
    best: dict[str, Match] = {}
    for meta in metas:
        if not is_candidate(meta, include_hidden=include_hidden):
            continue
        hit = _keyword_hit(meta, role.keywords)
        if role.require_keyword and not hit:
            continue
        for matcher, unclassified_only in zip(role.matchers, role.unclassified_ok, strict=True):
            if unclassified_only and meta.device_class is not None:
                continue  # a contradicting device class (humidity in %) excludes the entity
            criteria = matched_criteria(matcher, meta)
            if criteria is None:
                continue
            score = sum(WEIGHTS[c] for c in criteria)
            if is_hidden(meta):
                score -= HIDDEN_MALUS
            names = criteria
            if hit and role.keywords:
                score += NAME_WEIGHT
                names = (*criteria, "name")
            current = best.get(meta.entity_id)
            if current is None or score > current.score:
                best[meta.entity_id] = Match(meta, score, names)
    return sorted(best.values(), key=lambda m: (-m.score, m.meta.entity_id))


def normalise_name(name: str | None) -> str:
    """Case- and whitespace-insensitive form of a device or entity name."""
    return " ".join((name or "").casefold().split())


def detect_template_role(
    role: RoleSpec,
    metas: Iterable[EntityMeta],
    *,
    same_device_id: str | None = None,
    include_hidden: bool = False,
    name_hint: str | None = None,
    device_name_of: Callable[[str | None], str | None] | None = None,
) -> list[Match]:
    """Ranked matches for a template role (``same_device_id`` = device of the base role).

    ``name_hint`` is the device name of an entity the user already chose (the climate entity);
    for a role without ``same_device_as`` (a power sensor of another integration) candidates
    whose device name equals it, or whose friendly name contains it, rank first
    (``name_affinity``). This is only a ranking hint: nothing is dropped, and no template
    schema feature is involved.
    """
    candidates = [m for m in metas if is_candidate(m, include_hidden=include_hidden)]
    ranked = rank_entities(role.matcher, candidates, same_device_id=same_device_id)
    hint = normalise_name(name_hint)
    matches: list[Match] = []
    for meta, score in ranked:
        criteria = _criteria(role, meta, same_device_id)
        bonus = 0
        if hint and _shares_name(meta, hint, device_name_of):
            bonus = NAME_AFFINITY_WEIGHT
            criteria = (*criteria, "name_affinity")
        malus = HIDDEN_MALUS if is_hidden(meta) else 0
        matches.append(Match(meta, score + bonus - malus, criteria))
    matches.sort(key=lambda m: (-m.score, m.meta.entity_id))
    return matches


def _shares_name(
    meta: EntityMeta, hint: str, device_name_of: Callable[[str | None], str | None] | None
) -> bool:
    device = normalise_name(device_name_of(meta.device_id) if device_name_of else None)
    return device == hint or hint in normalise_name(meta.name)


def _criteria(role: RoleSpec, meta: EntityMeta, same_device_id: str | None) -> tuple[str, ...]:
    return matched_criteria(role.matcher, meta, same_device_id=same_device_id) or ()


def criteria_keys(criteria: Iterable[str]) -> tuple[str, ...]:
    """Catalogue keys of matcher criterion names (unknown names are dropped)."""
    return tuple(CRITERION_KEYS[c] for c in criteria if c in CRITERION_KEYS)


def describe(
    matches: Iterable[Match],
    *,
    live: EntityCache,
    catalogue: EntityCatalogue,
) -> list[DetectedEntity]:
    """Attach name, area/device names and the current value to matches.

    The value comes from the live cache when the entity is subscribed, otherwise from the
    catalogue's browse copy; an entity in neither reads ``unavailable``.
    """
    result: list[DetectedEntity] = []
    for match in matches:
        meta = match.meta
        sample = live.sample(meta.entity_id) or catalogue.browse.sample(meta.entity_id)
        cached = live.get(meta.entity_id) or catalogue.browse.get(meta.entity_id)
        state = cached.state.state if cached is not None else "unavailable"
        device = catalogue.device(meta.device_id)
        result.append(
            DetectedEntity(
                entity_id=meta.entity_id,
                name=meta.name or meta.entity_id,
                domain=meta.domain,
                platform=meta.platform,
                device_class=meta.device_class or "",
                unit=meta.unit or "",
                state_class=meta.state_class,
                area_id=meta.area_id,
                area_name=catalogue.area_name(meta.area_id),
                device_id=meta.device_id,
                device_name=device.name if device is not None else None,
                score=match.score,
                criteria=criteria_keys(match.criteria),
                state=state,
                available=state not in UNAVAILABLE_STATES,
                value=sample.value if sample is not None else None,
                quality=sample.quality if sample is not None else Quality.UNAVAILABLE,
            )
        )
    return result


def legacy_entities(
    metas: Iterable[EntityMeta],
    *,
    live: EntityCache,
    catalogue: EntityCatalogue,
    include_hidden: bool = False,
) -> list[DetectedEntity]:
    """Phase 1 picker: every power (W, kW) and battery (%) sensor, sorted by entity id.

    Hidden/diagnostic/disabled entities are left out now that the registry is known
    (``include_hidden`` keeps the first two kinds).
    """
    allowed = {m.entity_id: m for m in metas if is_candidate(m, include_hidden=include_hidden)}
    states = [
        cached.state
        for entity_id in allowed
        if (cached := catalogue.browse.get(entity_id) or live.get(entity_id)) is not None
    ]
    result: list[DetectedEntity] = []
    for candidate in entity_candidates(states):
        meta = allowed[candidate.entity_id]
        sample = live.sample(candidate.entity_id) or catalogue.browse.sample(candidate.entity_id)
        device = catalogue.device(meta.device_id)
        result.append(
            DetectedEntity(
                entity_id=candidate.entity_id,
                name=candidate.name,
                domain=meta.domain,
                platform=meta.platform,
                device_class=candidate.device_class,
                unit=candidate.unit.value,
                state_class=meta.state_class,
                area_id=meta.area_id,
                area_name=catalogue.area_name(meta.area_id),
                device_id=meta.device_id,
                device_name=device.name if device is not None else None,
                score=0,
                criteria=(),
                state=candidate.state,
                available=candidate.available,
                value=sample.value if sample is not None else None,
                quality=sample.quality if sample is not None else Quality.UNAVAILABLE,
            )
        )
    return result
