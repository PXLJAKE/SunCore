# SPDX-License-Identifier: Apache-2.0
"""Wire models of the HTTP and WebSocket API.

The Pydantic models are the schema: OpenAPI is generated from them and the frontend
derives its TypeScript types from it (``scripts/export_openapi.py``). Reasons travel as
``{key, args}`` only; rendering is the frontend's job (ICU catalogue).
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from suncore.api.services import CandidateInfo, HealthReport
from suncore.core.balance import Flow, FlowSnapshot
from suncore.core.decisions import Decision

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
    entity_id: str
    name: str
    device_class: str
    unit: str
    state: str
    available: bool

    @classmethod
    def from_info(cls, info: CandidateInfo) -> EntityCandidateOut:
        return cls(
            entity_id=info.entity_id,
            name=info.name,
            device_class=info.device_class,
            unit=info.unit,
            state=info.state,
            available=info.available,
        )


class ImportResultOut(_Model):
    imported_sections: list[str]


class ErrorOut(_Model):
    detail: str


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
    "DecisionsMessage",
    "EntityCandidateOut",
    "ErrorOut",
    "FlowOut",
    "FlowsMessage",
    "FlowsOut",
    "HealthMessage",
    "HealthOut",
    "ImportResultOut",
    "InfoOut",
    "LiveMessage",
    "SnapshotMessage",
]
