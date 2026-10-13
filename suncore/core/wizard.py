# SPDX-License-Identifier: Apache-2.0
"""Wizard logic that needs no I/O: the system check and the live preview of a mapping.

* :func:`run_system_check` answers the question of wizard step 10: are the mapped entities
  fresh, are their units plausible, does the power balance close, do the signs look right and
  are the required configuration sections present? Every finding is a
  :class:`~suncore.core.decisions.Reason` with a severity, rendered by the frontend catalogue.
* :func:`preview` reads the current value of every entity in an *unsaved* configuration
  fragment, with quality, unit and the value converted to the base unit (sign inversion
  applied), so the wizard can show "the battery is charging at 2.1 kW right now".

The balance tolerance is deliberately generous (inverters and meters sample at different
times); the check warns, it never blocks saving.
"""

from __future__ import annotations

import math
import re
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Final, Literal

from suncore.core.balance import FlowSnapshot, compute_flows
from suncore.core.decisions import Reason
from suncore.core.signals import Quality, Sample
from suncore.core.site import ENTITY_ID_PATTERN, EntityRef, SiteConfig
from suncore.core.units import Dimension, apply_invert, to_base

Severity = Literal["ok", "warn", "error"]

BALANCE_TOLERANCE_ABS_W: Final = 300.0
BALANCE_TOLERANCE_REL: Final = 0.15
"""The balance closes if ``|pv + grid - battery - house|`` is below the larger of the two."""
SOC_FULL_PCT: Final = 99.0
SOC_EMPTY_PCT: Final = 1.0
SIGN_EVIDENCE_W: Final = 300.0
PV_NEGATIVE_W: Final = -100.0

PREVIEW_MAX_REFS: Final = 64
PREVIEW_MAX_DEPTH: Final = 8

_ENTITY_ID: Final = re.compile(ENTITY_ID_PATTERN)


@dataclass(frozen=True, slots=True)
class CheckItem:
    """One finding of the system check."""

    severity: Severity
    reason: Reason
    subject: str | None = None
    """Entity id or section name the finding is about."""


_SEVERITY_ORDER: Final[tuple[Severity, ...]] = ("ok", "warn", "error")


def worst_severity(items: Iterable[CheckItem]) -> Severity:
    return max((i.severity for i in items), key=_SEVERITY_ORDER.index, default="ok")


def _item(
    severity: Severity, key: str, subject: str | None = None, **args: str | int | float | bool
) -> CheckItem:
    return CheckItem(severity, Reason(key=key, args=args), subject)


# --- which dimension does a role expect? ----------------------------------------------


def _typed_refs(site: SiteConfig) -> list[tuple[str, Dimension | None, EntityRef]]:
    """``(role path, expected dimension, ref)`` for every mapped entity of the site."""
    g = site.grid
    refs: list[tuple[str, Dimension | None, EntityRef | None]] = [
        ("grid.power", Dimension.POWER, g.power),
        ("grid.import_power", Dimension.POWER, g.import_power),
        ("grid.export_power", Dimension.POWER, g.export_power),
        ("grid.energy_import", Dimension.ENERGY, g.energy_import),
        ("grid.energy_export", Dimension.ENERGY, g.energy_export),
        ("grid.para14a_signal", None, g.para14a_signal),
    ]
    refs.extend((f"pv.{i}.power", Dimension.POWER, a.power) for i, a in enumerate(site.pv))
    if site.battery is not None:
        refs.append(("battery.power", Dimension.POWER, site.battery.power))
        refs.append(("battery.soc", Dimension.RATIO, site.battery.soc))
    refs.append(("house", Dimension.POWER, site.house))
    return [(path, dim, ref) for path, dim, ref in refs if ref is not None]


# --- system check -----------------------------------------------------------------


def _check_sections(site: SiteConfig | None, present: Collection[str]) -> list[CheckItem]:
    items: list[CheckItem] = []
    if site is None:
        items.append(_item("error", "wizard.check.site_missing", "site"))
    items.extend(
        _item("warn", "wizard.check.section_missing", name, section=name)
        for name in ("region", "tariff")
        if name not in present
    )
    if "forecast" not in present:  # optional: a note, never a warning
        items.append(_item("ok", "wizard.check.forecast_missing", "forecast"))
    return items


def _check_entities(
    refs: list[tuple[str, Dimension | None, EntityRef]], samples: Mapping[str, Sample]
) -> list[CheckItem]:
    items: list[CheckItem] = []
    for _path, _dim, ref in refs:
        sample = samples.get(ref.entity_id)
        if sample is None:
            items.append(
                _item(
                    "error", "wizard.check.entity_missing", ref.entity_id, entity_id=ref.entity_id
                )
            )
        elif sample.quality is Quality.INVALID:
            items.append(
                _item(
                    "error", "wizard.check.entity_invalid", ref.entity_id, entity_id=ref.entity_id
                )
            )
        elif sample.quality is Quality.UNAVAILABLE:
            items.append(
                _item(
                    "warn",
                    "wizard.check.entity_unavailable",
                    ref.entity_id,
                    entity_id=ref.entity_id,
                )
            )
        elif sample.quality is Quality.STALE:
            items.append(
                _item("warn", "wizard.check.entity_stale", ref.entity_id, entity_id=ref.entity_id)
            )
    if not items:
        items.append(_item("ok", "wizard.check.entities_fresh", count=len(refs)))
    return items


def _check_units(
    refs: list[tuple[str, Dimension | None, EntityRef]], samples: Mapping[str, Sample]
) -> list[CheckItem]:
    items: list[CheckItem] = []
    for _path, expected, ref in refs:
        sample = samples.get(ref.entity_id)
        if sample is None or expected is None:
            continue
        if sample.unit is None:
            if sample.quality in (Quality.OK, Quality.STALE):  # a value without a unit
                items.append(
                    _item(
                        "error", "wizard.check.unit_unknown", ref.entity_id, entity_id=ref.entity_id
                    )
                )
            continue
        if sample.unit.dimension is not expected:
            items.append(
                _item(
                    "error",
                    "wizard.check.unit_mismatch",
                    ref.entity_id,
                    entity_id=ref.entity_id,
                    expected=expected.value,
                    unit=sample.unit.value,
                )
            )
        elif (
            expected is Dimension.RATIO
            and sample.value is not None
            and not 0.0 <= sample.value <= 100.0
        ):
            items.append(
                _item(
                    "error",
                    "wizard.check.soc_range",
                    ref.entity_id,
                    entity_id=ref.entity_id,
                    value=sample.value,
                )
            )
    if not items:
        items.append(_item("ok", "wizard.check.units_ok"))
    return items


def _tolerance(*values: float) -> float:
    return max(BALANCE_TOLERANCE_ABS_W, BALANCE_TOLERANCE_REL * max(abs(v) for v in values))


def _check_balance(site: SiteConfig, flows: FlowSnapshot) -> list[CheckItem]:
    pv, grid, battery, house = (
        flows.pv_w.value,
        flows.grid_w.value,
        flows.battery_w.value,
        flows.house_w.value,
    )
    if pv is None or grid is None or battery is None or house is None:
        return [_item("warn", "wizard.check.balance_unknown")]
    tolerance = _tolerance(pv, grid, battery, house)
    if site.house is None:
        # The house load is derived from the balance, so it always closes; a clearly
        # negative load still means a sign is wrong somewhere.
        if house < -tolerance:
            return [
                _item("warn", "wizard.check.house_negative", house_w=round(house, 1)),
                *_sign_suspects(pv, grid, battery, house, tolerance, measured_house=False),
            ]
        return [_item("ok", "wizard.check.balance_derived")]
    residual = pv + grid - battery - house
    if abs(residual) <= tolerance:
        return [
            _item(
                "ok",
                "wizard.check.balance_ok",
                residual_w=round(residual, 1),
                tolerance_w=round(tolerance, 1),
            )
        ]
    return [
        _item(
            "warn",
            "wizard.check.balance_off",
            residual_w=round(residual, 1),
            tolerance_w=round(tolerance, 1),
        ),
        *_sign_suspects(pv, grid, battery, house, tolerance, measured_house=True),
    ]


def _sign_suspects(
    pv: float, grid: float, battery: float, house: float, tolerance: float, *, measured_house: bool
) -> list[CheckItem]:
    """Parts whose sign flip alone would close the balance (measured house only)."""
    if not measured_house:
        return []
    candidates = {
        "grid": -grid + pv - battery - house,
        "battery": grid + pv + battery - house,
        "pv": grid - pv - battery - house,
    }
    values = {"grid": grid, "battery": battery, "pv": pv}
    closing = [
        part
        for part, residual in candidates.items()
        if abs(residual) <= tolerance and abs(2 * values[part]) > tolerance
    ]
    if len(closing) != 1:
        return []  # no evidence, or several explanations: do not guess
    return [_item("warn", "wizard.check.sign_suspect", closing[0], part=closing[0])]


def _check_battery_and_pv_signs(site: SiteConfig, flows: FlowSnapshot) -> list[CheckItem]:
    items: list[CheckItem] = []
    pv = flows.pv_w.value
    if pv is not None and pv < PV_NEGATIVE_W:
        items.append(_item("warn", "wizard.check.pv_negative", pv_w=round(pv, 1)))
    if site.battery is None:
        return items
    battery, soc = flows.battery_w.value, flows.battery_soc_pct.value
    if battery is None or soc is None:
        return items
    full_charging = soc >= SOC_FULL_PCT and battery > SIGN_EVIDENCE_W
    empty_discharging = soc <= SOC_EMPTY_PCT and battery < -SIGN_EVIDENCE_W
    if full_charging or empty_discharging:
        items.append(
            _item(
                "warn",
                "wizard.check.battery_sign_soc",
                site.battery.power.entity_id,
                soc_pct=round(soc, 1),
                battery_w=round(battery, 1),
            )
        )
    return items


def run_system_check(
    site: SiteConfig | None, samples: Mapping[str, Sample], present_sections: Collection[str]
) -> list[CheckItem]:
    """All findings, most basic first. ``samples`` are by entity id (the live cache)."""
    items = _check_sections(site, present_sections)
    if site is None:
        return items
    refs = _typed_refs(site)
    items.extend(_check_entities(refs, samples))
    items.extend(_check_units(refs, samples))
    unit_errors = (
        "wizard.check.unit_unknown",
        "wizard.check.unit_mismatch",
        "wizard.check.soc_range",
    )
    if any(i.reason.key in unit_errors for i in items):
        return items  # a balance over wrong units would only add noise
    flows = compute_flows(site, samples)
    items.extend(_check_balance(site, flows))
    items.extend(_check_battery_and_pv_signs(site, flows))
    battery_doubt = any(
        i.reason.key == "wizard.check.battery_sign_soc"
        or (i.reason.key == "wizard.check.sign_suspect" and i.subject == "battery")
        for i in items
    )
    if (
        site.battery is not None
        and not battery_doubt
        and flows.battery_w.value is not None
        and flows.battery_soc_pct.value is not None
    ):
        items.append(_item("ok", "wizard.check.battery_sign_plausible"))
    return items


# --- preview ----------------------------------------------------------------------


class PreviewTooLargeError(ValueError):
    """The fragment names more entities than :data:`PREVIEW_MAX_REFS` or nests too deep."""


@dataclass(frozen=True, slots=True)
class PreviewItem:
    """Live reading of one mapped entity."""

    path: str
    entity_id: str
    invert_sign: bool
    raw_value: float | None
    unit: str | None
    quality: Quality
    base_value: float | None
    """Value in W, Wh or % with ``invert_sign`` applied; ``None`` without a usable value."""
    base_unit: str | None
    expected: str | None
    """Dimension the role expects (``power``, ``energy``, ``ratio``), if the path says."""
    unit_ok: bool | None
    """Whether the unit matches the expectation; ``None`` if nothing was expected."""


def expected_dimension(path: str) -> Dimension | None:
    """Dimension a fragment path implies, from its last segment (``None`` if unknown)."""
    segment = path.rsplit(".", 1)[-1].lower()
    if "soc" in segment:
        return Dimension.RATIO
    if "energy" in segment:
        return Dimension.ENERGY
    if "power" in segment or segment in ("house", "house_power"):
        return Dimension.POWER
    return None


def collect_refs(fragment: Any) -> list[tuple[str, str, bool]]:
    """Every entity reference in a configuration fragment as ``(path, entity_id, invert)``.

    A reference is any object with a valid ``entity_id``; objects inside it are not searched.

    Raises:
        PreviewTooLargeError: more than :data:`PREVIEW_MAX_REFS` references or deeper than
            :data:`PREVIEW_MAX_DEPTH`.
    """
    found: list[tuple[str, str, bool]] = []

    def walk(node: Any, path: str, depth: int) -> None:
        if depth > PREVIEW_MAX_DEPTH:
            msg = "fragment nests too deep"
            raise PreviewTooLargeError(msg)
        if isinstance(node, Mapping):
            entity_id = node.get("entity_id")
            if isinstance(entity_id, str) and _ENTITY_ID.match(entity_id):
                found.append((path, entity_id, node.get("invert_sign") is True))
                if len(found) > PREVIEW_MAX_REFS:
                    msg = "fragment names too many entities"
                    raise PreviewTooLargeError(msg)
                return
            for key, value in node.items():
                walk(value, f"{path}.{key}" if path else str(key), depth + 1)
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{path}.{index}" if path else str(index), depth + 1)

    walk(fragment, "", 0)
    return found


def preview(fragment: Any, samples: Mapping[str, Sample]) -> list[PreviewItem]:
    """Live values of all entities in ``fragment`` (see :func:`collect_refs`)."""
    items: list[PreviewItem] = []
    for path, entity_id, invert in collect_refs(fragment):
        sample = samples.get(entity_id)
        expected = expected_dimension(path)
        if sample is None:
            items.append(
                PreviewItem(
                    path,
                    entity_id,
                    invert,
                    None,
                    None,
                    Quality.UNAVAILABLE,
                    None,
                    None,
                    expected.value if expected else None,
                    None,
                )
            )
            continue
        raw = sample.value
        unit = sample.unit
        base: float | None = None
        base_unit: str | None = None
        if raw is not None and unit is not None:
            converted = to_base(raw, unit)
            if math.isfinite(converted):
                base = apply_invert(converted, invert)
                base_unit = unit.base.value
        unit_ok = None if expected is None else (unit is not None and unit.dimension is expected)
        items.append(
            PreviewItem(
                path,
                entity_id,
                invert,
                raw,
                unit.value if unit is not None else None,
                sample.quality,
                base,
                base_unit,
                expected.value if expected else None,
                unit_ok,
            )
        )
    return items
