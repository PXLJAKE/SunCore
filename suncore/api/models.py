# SPDX-License-Identifier: Apache-2.0
"""Wire models of the HTTP and WebSocket API.

The Pydantic models are the schema: OpenAPI is generated from them and the frontend
derives its TypeScript types from it (``scripts/export_openapi.py``). Reasons travel as
``{key, args}`` only; rendering is the frontend's job (ICU catalogue).
"""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from suncore.api.services import HealthReport
from suncore.core.balance import Flow, FlowSnapshot
from suncore.core.controlstate import ControlState, DeviceModeRequest, ModeRequest
from suncore.core.decisions import Decision, Reason
from suncore.core.detection import DetectedEntity, EnergySuggestions
from suncore.core.haentities import HaEntitiesStatus
from suncore.core.site import SiteConfig
from suncore.core.wizard import CheckItem, PreviewItem, worst_severity
from suncore.notify import NotifyStatus, TestResult
from suncore.planner.views import PlanSummary
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


class ValidationIssueOut(_Model):
    """One invalid field. ``key`` is a catalogue key, ``args`` its ICU placeholders."""

    loc: list[str | int]
    """Path of the field, starting with ``body``/``query``/``path``."""
    key: str
    args: dict[str, str | int | float | bool]


class ValidationErrorOut(_Model):
    """422 body of a request that failed validation."""

    detail: str
    """Always ``api.error.validation``."""
    issues: list[ValidationIssueOut]


class ImportResultOut(_Model):
    imported_sections: list[str]


class ErrorOut(_Model):
    detail: str


class VerificationRefusedOut(_Model):
    """409 of the inverter limit verification (PR 7-J): why it cannot start or go on."""

    detail: str
    reasons: list[Reason]


# --- control (Phase 3 WP9) -----------------------------------------------------


class EmergencyStopOut(_Model):
    """State of the global emergency stop after the call."""

    active: bool


# --- Home Assistant entities (Phase 6 WP3, ADR-0016) ---------------------------


class HaEntitiesStatusOut(_Model):
    """``GET /api/v1/ha-entities/status``: the MQTT entity surface."""

    state: Literal["disabled", "no_broker", "connecting", "connected", "error"]
    source: Literal["supervisor", "manual"] | None
    """Where the broker comes from (``null`` while none is looked up)."""
    entity_count: int
    """Entities announced to Home Assistant (0 while not connected)."""
    reason_key: str | None
    """Why the surface is not connected (``mqtt.reason.*``, rendered by the frontend)."""
    hint_key: str | None
    """``mqtt.hint.install_broker`` while no broker was found, else ``null``."""

    @classmethod
    def from_core(cls, status: HaEntitiesStatus) -> HaEntitiesStatusOut:
        return cls(
            state=status.state.value,
            source=status.source,
            entity_count=status.entity_count,
            reason_key=status.reason_key,
            hint_key="mqtt.hint.install_broker" if status.state.value == "no_broker" else None,
        )


# --- notifications (Phase 6 WP5, ADR-0016 decision 7) ---------------------------------------


class NotificationsStatusOut(_Model):
    """``GET /api/v1/notifications/status``: do push and the confirm buttons work?"""

    actions: Literal["pending", "active", "refused"]
    """``active``: Home Assistant lets SunCore receive taps on notification buttons.
    ``refused``: it does not; messages carry no buttons and point to the panel."""
    reason_key: str | None
    """Why something does not work as configured (``notify.reason.*``), else ``null``."""
    push_targets: int
    button_targets: int
    """Push targets that can carry buttons (companion app services)."""
    pushed_today: int
    open_buttons: int
    """Proposals that currently have working buttons."""
    entity_targets: list[str]
    """``notify.*`` entities Home Assistant knows, for choosing ``entity`` targets."""

    @classmethod
    def from_core(cls, status: NotifyStatus, entities: list[str]) -> NotificationsStatusOut:
        return cls(
            actions=status.actions.value,
            reason_key=status.reason_key,
            push_targets=status.push_targets,
            button_targets=status.button_targets,
            pushed_today=status.pushed_today,
            open_buttons=status.open_buttons,
            entity_targets=entities,
        )


class NotificationTestOut(_Model):
    """Result of ``POST /api/v1/notifications/test``."""

    sent: int
    failed: int
    reason_key: str | None
    """``notify.reason.no_targets`` or ``notify.reason.test_rate_limited`` when nothing was sent."""

    @classmethod
    def from_core(cls, result: TestResult) -> NotificationTestOut:
        return cls(sent=result.sent, failed=result.failed, reason_key=result.reason_key)


class HaEntitiesRepublishOut(_Model):
    """Result of ``POST /api/v1/ha-entities/republish``."""

    republished: bool
    """``false`` = there is no broker session to publish on."""


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
    kind: Literal["device", "tariff", "forecast", "region", "battery", "inverter"]
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


_ABSOLUTE_PATH = re.compile(r"(?<![\w.\-/\\])(?:[A-Za-z]:)?(?:[\\/][^\\/\s:`'\"<>|,;()]+){2,}")


def _short_path(match: re.Match[str]) -> str:
    parts = re.split(r"[\\/]", match.group(0))
    return "/".join(parts[-2:])


def _error_out(err: TemplateFileError) -> TemplateErrorOut:
    # Only the last two path parts (<kind>/<id>.yaml): the data directory is nobody's business,
    # neither in `file` nor in loader messages that quote paths (e.g. "also /data/...").
    return TemplateErrorOut(
        file="/".join(err.path.parts[-2:]),
        message=_ABSOLUTE_PATH.sub(_short_path, err.message),
    )


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
    solar_forecast_entries: list[str] = Field(default_factory=list)
    """Config entry ids linked to the dashboard's solar sources: the choices for a Forecast.Solar
    source's ``config_entry_id`` (several entries need an explicit choice)."""
    solar_forecast_labels: dict[str, str] = Field(default_factory=dict)
    """Readable label per entry id where the device registry names one."""

    @classmethod
    def from_core(cls, e: EnergySuggestions) -> EnergyPrefsOut:
        return cls(
            configured=e.configured,
            suggestions=[
                SuggestionOut(path=s.path, value=s.value, reason=s.reason) for s in e.suggestions
            ],
            notes=list(e.notes),
            site=dict(e.site),
            solar_forecast_entries=list(e.solar_forecast_entries),
            solar_forecast_labels=dict(e.solar_forecast_labels),
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
    control: ControlState


class FlowsMessage(_Model):
    type: Literal["flows"] = "flows"
    flows: FlowsOut | None


class HealthMessage(_Model):
    type: Literal["health"] = "health"
    health: HealthOut


class DecisionsMessage(_Model):
    type: Literal["decisions"] = "decisions"
    decisions: list[Decision]


class ControlMessage(_Model):
    """The control state changed (tick, decision, mode, emergency stop, override, proposal)."""

    type: Literal["control"] = "control"
    control: ControlState


class PlanMessage(_Model):
    """A new plan was computed (summary only; fetch ``GET /api/v1/plan`` for the intervals)."""

    type: Literal["plan"] = "plan"
    plan: PlanSummary


LiveMessage = Annotated[
    SnapshotMessage
    | FlowsMessage
    | HealthMessage
    | DecisionsMessage
    | ControlMessage
    | PlanMessage,
    Field(discriminator="type"),
]
"""Everything the server sends on ``/api/v1/live`` (discriminated by ``type``)."""

__all__ = [
    "CheckItemOut",
    "CheckOut",
    "CheckRequest",
    "ConfigHistoryEntryOut",
    "ControlMessage",
    "DecisionsMessage",
    "DeviceModeRequest",
    "EmergencyStopOut",
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
    "ModeRequest",
    "PlanMessage",
    "PreviewItemOut",
    "PreviewOut",
    "SnapshotMessage",
    "SuggestionOut",
    "TemplateDetailOut",
    "TemplateErrorOut",
    "TemplateListOut",
    "TemplateSummaryOut",
    "ValidationErrorOut",
    "ValidationIssueOut",
    "template_list",
]
