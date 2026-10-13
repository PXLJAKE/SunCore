# SPDX-License-Identifier: Apache-2.0
"""Wire models of the HTTP and WebSocket API.

The Pydantic models are the schema: OpenAPI is generated from them and the frontend
derives its TypeScript types from it (``scripts/export_openapi.py``). Reasons travel as
``{key, args}`` only; rendering is the frontend's job (ICU catalogue).
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from suncore.api.services import HealthReport
from suncore.core.balance import Flow, FlowSnapshot
from suncore.core.decisions import Decision, Reason
from suncore.core.detection import DetectedEntity, EnergySuggestions
from suncore.core.site import SiteConfig
from suncore.core.wizard import CheckItem, PreviewItem, worst_severity
from suncore.storage import HistoryEntry
from suncore.templates import (
    DeviceTemplate,
    RegionTemplate,
    Template,
    TemplateCatalog,
    TemplateFileError,
)

QualityName = Literal["ok", "stale", "unavailable", "invalid"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FlowOut(_Model):
    """A value in W (or % for the state of charge) with its data quality."""

    value: float | None
    quality: QualityName

    @classmethod
    def from_core(cls, flow: Flow) -> FlowOut:
        return cls(value=flow.value, quality=flow.quality.value)


class FlowsOut(_Model):
    """Site flows in W, core sign convention (grid + = import, battery + = charging)."""

    grid_w: FlowOut
    pv_w: FlowOut
    battery_w: FlowOut
    house_w: FlowOut
    battery_soc_pct: FlowOut
    ts: AwareDatetime | None

    @classmethod
    def from_core(cls, snap: FlowSnapshot) -> FlowsOut:
        return cls(
            grid_w=FlowOut.from_core(snap.grid_w),
            pv_w=FlowOut.from_core(snap.pv_w),
            battery_w=FlowOut.from_core(snap.battery_w),
            house_w=FlowOut.from_core(snap.house_w),
            battery_soc_pct=FlowOut.from_core(snap.battery_soc_pct),
            ts=snap.ts,
        )


class HealthOut(_Model):
    ok: bool
    ha_state: str
    ha_last_update_age_s: float | None
    db_ok: bool
    version: str

    @classmethod
    def from_report(cls, report: HealthReport) -> HealthOut:
        return cls(
            ok=report.ok,
            ha_state=report.ha_state,
            ha_last_update_age_s=report.ha_last_update_age_s,
            db_ok=report.db_ok,
            version=report.version,
        )


class InfoOut(_Model):
    version: str
    demo: bool
    mode: Literal["shadow", "assisted", "automatic"]


class EntityCandidateOut(_Model):
    """An entity offered for a role.

    ``device_class`` and ``unit`` are empty strings when unknown. ``score`` is 0 and
    ``criteria`` empty for the unranked Phase 1 list. ``value`` is the raw state in ``unit``
    (no conversion, no sign inversion).
    """

    entity_id: str
    name: str
    device_class: str
    unit: str
    state: str
    available: bool
    domain: str
    platform: str | None
    state_class: str | None
    area_id: str | None
    area_name: str | None
    device_id: str | None
    device_name: str | None
    score: int
    criteria: list[str]
    """Catalogue keys ``detect.criterion.<name>`` of the criteria that matched."""
    value: float | None
    quality: QualityName

    @classmethod
    def from_detected(cls, e: DetectedEntity) -> EntityCandidateOut:
        return cls(
            entity_id=e.entity_id,
            name=e.name,
            device_class=e.device_class,
            unit=e.unit,
            state=e.state,
            available=e.available,
            domain=e.domain,
            platform=e.platform,
            state_class=e.state_class,
            area_id=e.area_id,
            area_name=e.area_name,
            device_id=e.device_id,
            device_name=e.device_name,
            score=e.score,
            criteria=list(e.criteria),
            value=e.value,
            quality=e.quality.value,
        )


class ImportResultOut(_Model):
    imported_sections: list[str]


class ErrorOut(_Model):
    detail: str


# --- config history ------------------------------------------------------------


class ConfigHistoryEntryOut(_Model):
    """A replaced section body that can be restored."""

    id: int
    section: str
    schema_version: int
    replaced_at: str
    body: dict[str, Any]

    @classmethod
    def from_entry(cls, e: HistoryEntry) -> ConfigHistoryEntryOut:
        return cls(
            id=e.id,
            section=e.section,
            schema_version=e.schema_version,
            replaced_at=e.replaced_at,
            body=e.body,
        )


class HistoryRestoreOut(_Model):
    section: str
    body: dict[str, Any]
    """The restored (now current) section document, by alias."""


# --- templates ------------------------------------------------------------------


class TestedWithOut(_Model):
    __test__ = False

    integration: str
    version: str


class TemplateSummaryOut(_Model):
    """Gallery entry of a template."""

    id: str
    kind: Literal["device", "tariff", "forecast", "region"]
    name: str
    """i18n key (``template.<id>.name``)."""
    origin: Literal["builtin", "user"]
    overridden: bool
    """A user template replaces the built-in one of the same id."""
    capabilities: list[str]
    tested_with: list[TestedWithOut]
    unverified: list[str]
    device_kind: str | None
    roles: list[str]


class TemplateErrorOut(_Model):
    """A user template that failed to load. ``message`` is technical English."""

    file: str
    message: str


class TemplateListOut(_Model):
    templates: list[TemplateSummaryOut]
    errors: list[TemplateErrorOut]


class TemplateDetailOut(_Model):
    origin: Literal["builtin", "user"]
    template: Template


def _summary(template: Template, catalog: TemplateCatalog) -> TemplateSummaryOut:
    roles = [] if isinstance(template, RegionTemplate) else list(template.roles)
    device_kind = template.device_kind.value if isinstance(template, DeviceTemplate) else None
    return TemplateSummaryOut(
        id=template.id,
        kind=template.kind,
        name=template.name,
        origin=catalog.origins.get(template.id, "builtin"),
        overridden=template.id in catalog.overridden,
        capabilities=[c.value for c in template.capabilities],
        tested_with=[
            TestedWithOut(integration=t.integration, version=t.version)
            for t in template.tested_with
        ],
        unverified=list(template.unverified),
        device_kind=device_kind,
        roles=roles,
    )


def _error_out(err: TemplateFileError) -> TemplateErrorOut:
    # Only the last two path parts (<kind>/<id>.yaml): the data directory is nobody's business.
    return TemplateErrorOut(file="/".join(err.path.parts[-2:]), message=err.message)


def template_list(catalog: TemplateCatalog, kind: str | None = None) -> TemplateListOut:
    items = [t for t in catalog.templates.values() if kind is None or t.kind == kind]
    items.sort(key=lambda t: (t.kind, t.id))
    return TemplateListOut(
        templates=[_summary(t, catalog) for t in items],
        errors=[_error_out(e) for e in catalog.errors],
    )


# --- wizard -----------------------------------------------------------------------


class SuggestionOut(_Model):
    path: str
    """SiteConfig field, e.g. ``battery.power`` or ``pv.0.power``."""
    value: Any
    """An entity reference (``entity_id``, ``invert_sign``) or a number (``capacity_wh``)."""
    reason: Reason


class EnergyPrefsOut(_Model):
    """Energy dashboard import: suggestions only, never applied by the server."""

    configured: bool
    suggestions: list[SuggestionOut]
    notes: list[Reason]
    site: dict[str, Any]
    """The suggestions as a partial site document."""

    @classmethod
    def from_core(cls, e: EnergySuggestions) -> EnergyPrefsOut:
        return cls(
            configured=e.configured,
            suggestions=[
                SuggestionOut(path=s.path, value=s.value, reason=s.reason) for s in e.suggestions
            ],
            notes=list(e.notes),
            site=dict(e.site),
        )


SeverityName = Literal["ok", "warn", "error"]


class CheckItemOut(_Model):
    severity: SeverityName
    reason: Reason
    subject: str | None

    @classmethod
    def from_core(cls, i: CheckItem) -> CheckItemOut:
        return cls(severity=i.severity, reason=i.reason, subject=i.subject)


class CheckRequest(_Model):
    """Optional body of the system check: check this unsaved site instead of the stored one."""

    site: SiteConfig | None = None


class CheckOut(_Model):
    severity: SeverityName
    """The worst severity of ``items``."""
    items: list[CheckItemOut]

    @classmethod
    def from_core(cls, items: list[CheckItem]) -> CheckOut:
        return cls(severity=worst_severity(items), items=[CheckItemOut.from_core(i) for i in items])


class PreviewItemOut(_Model):
    path: str
    entity_id: str
    invert_sign: bool
    raw_value: float | None
    unit: str | None
    quality: QualityName
    base_value: float | None
    """Value in W, Wh or % after ``invert_sign``; ``null`` without a usable value."""
    base_unit: str | None
    expected: Literal["power", "energy", "ratio"] | None
    unit_ok: bool | None

    @classmethod
    def from_core(cls, i: PreviewItem) -> PreviewItemOut:
        return cls.model_validate(
            {
                "path": i.path,
                "entity_id": i.entity_id,
                "invert_sign": i.invert_sign,
                "raw_value": i.raw_value,
                "unit": i.unit,
                "quality": i.quality.value,
                "base_value": i.base_value,
                "base_unit": i.base_unit,
                "expected": i.expected,
                "unit_ok": i.unit_ok,
            }
        )


class PreviewOut(_Model):
    items: list[PreviewItemOut]


# --- live feed ---------------------------------------------------------------


class SnapshotMessage(_Model):
    type: Literal["snapshot"] = "snapshot"
    flows: FlowsOut | None
    health: HealthOut
    decisions: list[Decision]


class FlowsMessage(_Model):
    type: Literal["flows"] = "flows"
    flows: FlowsOut | None


class HealthMessage(_Model):
    type: Literal["health"] = "health"
    health: HealthOut


class DecisionsMessage(_Model):
    type: Literal["decisions"] = "decisions"
    decisions: list[Decision]


LiveMessage = Annotated[
    SnapshotMessage | FlowsMessage | HealthMessage | DecisionsMessage,
    Field(discriminator="type"),
]
"""Everything the server sends on ``/api/v1/live`` (discriminated by ``type``)."""

__all__ = [
    "CheckItemOut",
    "CheckOut",
    "CheckRequest",
    "ConfigHistoryEntryOut",
    "DecisionsMessage",
    "EnergyPrefsOut",
    "EntityCandidateOut",
    "ErrorOut",
    "FlowOut",
    "FlowsMessage",
    "FlowsOut",
    "HealthMessage",
    "HealthOut",
    "HistoryRestoreOut",
    "ImportResultOut",
    "InfoOut",
    "LiveMessage",
    "PreviewItemOut",
    "PreviewOut",
    "SnapshotMessage",
    "SuggestionOut",
    "TemplateDetailOut",
    "TemplateErrorOut",
    "TemplateListOut",
    "TemplateSummaryOut",
    "template_list",
]
