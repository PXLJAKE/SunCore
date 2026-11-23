# SPDX-License-Identifier: Apache-2.0
"""Energy dashboard preferences to suggested SiteConfig fragments (Phase 2 WP5).

Input is the result of ``energy/get_prefs`` (verified 2026-10-13 against
``homeassistant/components/energy/data.py``, see ``docs/research/ha-websocket-api.md``).
Output is a list of :class:`~suncore.core.detection.Suggestion` plus the same suggestions
folded into a partial site document. **Nothing is applied**: the wizard shows each
suggestion with its reason and the user accepts it.

Semantics relied upon:

* grid ``stat_rate``: Home Assistant counts power *from the grid* as positive, the same as
  SunCore, so no inversion;
* battery ``stat_rate``: **positive when discharging**, SunCore counts charging as positive,
  so the suggestion carries ``invert_sign: true``;
* if the user configured ``power_config.stat_rate_inverted`` (raw sensor with the opposite
  polarity) or the two-sensor form, HA creates a template sensor and stores *its* id as the
  top-level ``stat_rate`` - always in HA's convention. Only when the top-level value is
  missing do we fall back to the raw sensors of ``power_config``;
* ``stat_*`` fields are statistic ids: for sensors they equal the entity id, but an external
  statistic (``domain:name``) is not an entity and is never suggested (a note says so);
* ``capacity`` is in kWh (SunCore: Wh); solar ``stat_rate`` yields one PV array per source;
* tolerant of the older grid format (``flow_from`` / ``flow_to`` / ``power`` lists), which
  cores before the unified grid source (storage minor version 3) still return
  (``UNVERIFIED`` on a real older core; the shape is from the migration code).

Sources with energy statistics but no power sensor yield a note, not a guess.
"""

from __future__ import annotations

import itertools
import math
import re
from collections.abc import Collection, Mapping, Sequence
from typing import Any, Final

from suncore.core.decisions import Reason
from suncore.core.detection import EnergySuggestions, Suggestion
from suncore.core.site import ENTITY_ID_PATTERN

_ENTITY_ID: Final = re.compile(ENTITY_ID_PATTERN)
_NAME_MAX: Final = 80


def _entity(value: object) -> str | None:
    return value if isinstance(value, str) and _ENTITY_ID.match(value) else None


def _external(value: object) -> str | None:
    """A statistic id that is not an entity id (``domain:name`` external statistic)."""
    if isinstance(value, str) and value and not _ENTITY_ID.match(value):
        return value
    return None


def _ref(entity_id: str, *, invert: bool = False) -> dict[str, Any]:
    return {"entity_id": entity_id, "invert_sign": invert}


class _Builder:
    def __init__(self) -> None:
        self.suggestions: list[Suggestion] = []
        self.notes: list[Reason] = []
        self.site: dict[str, Any] = {}

    def add(self, path: str, value: Any, reason: Reason) -> None:
        self.suggestions.append(Suggestion(path, value, reason))
        node: Any = self.site
        parts = path.split(".")
        for part, nxt in itertools.pairwise(parts):
            if isinstance(node, list):
                index = int(part)
                while len(node) <= index:
                    node.append({})
                node = node[index]
            else:
                node = node.setdefault(part, [] if nxt.isdigit() else {})
        last = parts[-1]
        if isinstance(node, list):
            index = int(last)
            while len(node) <= index:
                node.append({})
            node[index] = value
        else:
            node[last] = value

    def note(self, key: str, **args: str | int | float | bool) -> None:
        self.notes.append(Reason(key=key, args=args))

    def statistic(self, value: object, source: str) -> str | None:
        """Entity id of a ``stat_*`` field, or ``None`` (with a note for external ones)."""
        entity = _entity(value)
        if entity is None and (external := _external(value)) is not None:
            self.note("wizard.energy.note.external_statistic", statistic_id=external, source=source)
        return entity


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _first_mapping(value: object) -> Mapping[str, Any]:
    if isinstance(value, list):
        for item in value:
            if isinstance(item, Mapping):
                return item
    return {}


def _candidates(entry: Mapping[str, Any], b: _Builder, source: str) -> list[tuple[str, bool]]:
    """Power sensors of a source in preference order, each with "opposite to HA's convention".

    The top-level ``stat_rate`` is always HA's convention (for a configured ``power_config``
    it is a generated template sensor). The raw sensors of ``power_config`` follow:
    ``stat_rate`` is HA's convention, ``stat_rate_inverted`` is the opposite.
    """
    result: list[tuple[str, bool]] = []
    top = b.statistic(entry.get("stat_rate"), source)
    if top is not None:
        result.append((top, False))
    config = _mapping(entry.get("power_config"))
    if (standard := _entity(config.get("stat_rate"))) is not None:
        result.append((standard, False))
    if (inverted := _entity(config.get("stat_rate_inverted"))) is not None:
        result.append((inverted, True))
    return result


def _pick(
    candidates: list[tuple[str, bool]], known: Collection[str] | None
) -> tuple[str, bool] | None:
    """First candidate Home Assistant has a state for; without ``known`` simply the first.

    The generated template sensor behind ``power_config`` may not exist (yet) while the raw
    sensor does; then the raw sensor is suggested, with its own polarity.
    """
    for candidate in candidates:
        if known is None or candidate[0] in known:
            return candidate
    return None


def _grid(sources: list[Mapping[str, Any]], b: _Builder, known: Collection[str] | None) -> None:
    if not sources:
        return
    if len(sources) > 1:
        b.note("wizard.energy.note.multiple_grid", count=len(sources))
    entry = sources[0]
    flow_from = _first_mapping(entry.get("flow_from"))
    flow_to = _first_mapping(entry.get("flow_to"))
    import_stat = entry.get("stat_energy_from", flow_from.get("stat_energy_from"))
    export_stat = entry.get("stat_energy_to", flow_to.get("stat_energy_to"))
    power = dict(entry)
    if "power" in entry:  # older format: a list of power entries
        power = {
            **_first_mapping(entry.get("power")),
            **{k: v for k, v in entry.items() if k != "power"},
        }
    candidates = _candidates(power, b, "grid")
    config = _mapping(power.get("power_config"))
    sensor_from = _entity(config.get("stat_rate_from"))
    sensor_to = _entity(config.get("stat_rate_to"))
    pair_known = (
        sensor_from is not None
        and sensor_to is not None
        and (known is None or (sensor_from in known and sensor_to in known))
    )
    rate = _pick(candidates, known)
    if rate is None and not pair_known:
        rate = candidates[0] if candidates else None  # nothing known: keep the HA-side id
    if rate is not None:
        entity_id, opposite = rate
        key = (
            "wizard.energy.reason.grid_rate_inverted"
            if opposite
            else "wizard.energy.reason.grid_rate"
        )
        b.add("grid.power", _ref(entity_id, invert=opposite), Reason(key=key))
    elif sensor_from is not None and sensor_to is not None:
        reason = Reason(key="wizard.energy.reason.grid_pair")
        b.add("grid.import_power", _ref(sensor_from), reason)
        b.add("grid.export_power", _ref(sensor_to), reason)
    else:
        b.note("wizard.energy.note.no_power_sensor", source="grid")
    imported = b.statistic(import_stat, "grid")
    if imported is not None:
        b.add(
            "grid.energy_import",
            _ref(imported),
            Reason(key="wizard.energy.reason.grid_energy_import"),
        )
    exported = b.statistic(export_stat, "grid")
    if exported is not None:
        b.add(
            "grid.energy_export",
            _ref(exported),
            Reason(key="wizard.energy.reason.grid_energy_export"),
        )


def _first(
    candidates: list[tuple[str, bool]], known: Collection[str] | None
) -> tuple[str, bool] | None:
    return _pick(candidates, known) or (candidates[0] if candidates else None)


def _solar(sources: list[Mapping[str, Any]], b: _Builder, known: Collection[str] | None) -> None:
    used: set[str] = set()
    index = 0
    for number, entry in enumerate(sources, start=1):
        raw_name = entry.get("name")
        name = (
            raw_name.strip()[:_NAME_MAX] if isinstance(raw_name, str) and raw_name.strip() else ""
        )
        name = name or f"PV {number}"
        if name in used:
            name = f"{name[: _NAME_MAX - 4]} #{number}"
        rate = _first(_candidates(entry, b, "solar"), known)
        if rate is None:
            b.note("wizard.energy.note.no_power_sensor", source="solar", name=name)
            continue
        entity_id, opposite = rate
        # A PV sensor is generation-positive in HA and SunCore; an inverted raw sensor needs a flip.
        used.add(name)
        reason = Reason(key="wizard.energy.reason.pv_rate", args={"name": name})
        b.add(f"pv.{index}.name", name, reason)
        b.add(f"pv.{index}.power", _ref(entity_id, invert=opposite), reason)
        index += 1


def _battery(sources: list[Mapping[str, Any]], b: _Builder, known: Collection[str] | None) -> None:
    if not sources:
        return
    if len(sources) > 1:
        b.note("wizard.energy.note.multiple_battery", count=len(sources))
    entry = sources[0]
    rate = _first(_candidates(entry, b, "battery"), known)
    if rate is not None:
        entity_id, opposite = rate
        # HA: + = discharging. SunCore: + = charging. The HA-convention sensor needs a flip,
        # a raw sensor that is already opposite to HA does not.
        invert = not opposite
        key = (
            "wizard.energy.reason.battery_rate"
            if invert
            else "wizard.energy.reason.battery_rate_inverted"
        )
        b.add("battery.power", _ref(entity_id, invert=invert), Reason(key=key))
    else:
        b.note("wizard.energy.note.no_power_sensor", source="battery")
    soc = b.statistic(entry.get("stat_soc"), "battery")
    if soc is not None:
        b.add("battery.soc", _ref(soc), Reason(key="wizard.energy.reason.battery_soc"))
    capacity = entry.get("capacity")
    if (
        isinstance(capacity, int | float)
        and not isinstance(capacity, bool)
        and math.isfinite(capacity)
        and capacity > 0
    ):
        b.add(
            "battery.capacity_wh",
            round(float(capacity) * 1000, 3),
            Reason(key="wizard.energy.reason.battery_capacity", args={"kwh": float(capacity)}),
        )


def suggest_from_prefs(
    prefs: Mapping[str, Any] | None, *, known_entity_ids: Collection[str] | None = None
) -> EnergySuggestions:
    """Suggestions from ``energy/get_prefs`` (``None`` = dashboard not configured).

    ``known_entity_ids`` (entities Home Assistant currently has states for) lets the result
    flag suggested entities that do not exist (yet).
    """
    if prefs is None:
        return EnergySuggestions(configured=False)
    sources = prefs.get("energy_sources")
    by_type: dict[str, list[Mapping[str, Any]]] = {"grid": [], "solar": [], "battery": []}
    if isinstance(sources, list):
        for source in sources:
            if isinstance(source, Mapping) and source.get("type") in by_type:
                by_type[str(source["type"])].append(source)
    b = _Builder()
    known = set(known_entity_ids) if known_entity_ids is not None else None
    _grid(by_type["grid"], b, known)
    _solar(by_type["solar"], b, known)
    _battery(by_type["battery"], b, known)
    if known is not None:
        for s in b.suggestions:
            value = s.value
            if isinstance(value, dict) and value.get("entity_id") not in known:
                b.note("wizard.energy.note.entity_unknown", entity_id=str(value["entity_id"]))
    return EnergySuggestions(
        configured=True,
        suggestions=tuple(b.suggestions),
        notes=tuple(b.notes),
        site=b.site,
        solar_forecast_entries=_solar_forecast_entries(by_type["solar"]),
    )


def _solar_forecast_entries(solar: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    """Config entry ids the solar sources link, in order, without duplicates."""
    found: dict[str, None] = {}
    for source in solar:
        linked = source.get("config_entry_solar_forecast")
        if isinstance(linked, list):
            for entry in linked:
                if isinstance(entry, str) and entry:
                    found[entry] = None
    return tuple(found)
