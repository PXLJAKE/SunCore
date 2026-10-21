# SPDX-License-Identifier: Apache-2.0
"""Fakes and helpers for API tests: in-memory ``Services`` and a real in-process uvicorn."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable, Collection, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel

from suncore.api import (
    EnergyPrefsFailedError,
    EntityQuery,
    HAUnavailableError,
    HealthReport,
    PeerPolicy,
    UnknownRoleError,
    UnknownTemplateError,
)
from suncore.core.balance import Flow, FlowSnapshot
from suncore.core.config import SECTION_MODELS, OperationConfig
from suncore.core.control import DeviceState, PowerEstimate
from suncore.core.controlstate import (
    ControlState,
    DeviceControlState,
    DeviceIdentityChangedError,
    OverrideCommand,
    OverrideUntilPastError,
    UnknownDeviceError,
    UnknownProposalError,
)
from suncore.core.decisions import (
    Decision,
    DecisionKind,
    Intent,
    OperatingMode,
    Origin,
    Reason,
    SwitchAction,
)
from suncore.core.detection import DetectedEntity, EnergySuggestions
from suncore.core.devices import DeviceKind
from suncore.core.signals import Quality
from suncore.core.site import EntityRef, GridConfig, PvArray, SiteConfig
from suncore.core.wizard import CheckItem, PreviewItem, PreviewTooLargeError
from suncore.forecast.accuracy import accuracy_view
from suncore.forecast.load_service import LoadForecastService
from suncore.forecast.views import ForecastAccuracyView, LoadForecastView, PvForecastView
from suncore.storage import (
    HistoryEntry,
    HistoryEntryNotFoundError,
    ImportRejectedError,
    UnknownSectionError,
)
from suncore.templates import TemplateCatalog, load_templates

TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "templates"

NOW = datetime(2026, 6, 21, 12, 0, tzinfo=UTC)
SECTION_NAMES = tuple(SECTION_MODELS)


def make_site() -> SiteConfig:
    return SiteConfig(
        grid=GridConfig(power=EntityRef(entity_id="sensor.grid")),
        pv=[PvArray(name="PV", power=EntityRef(entity_id="sensor.pv"))],
    )


def make_decision(n: int = 1) -> Decision:
    intent = Intent(
        id=f"i{n}",
        ts=NOW,
        asset_id="asset.heater",
        action=SwitchAction(on=True),
        reasons=(Reason(key="controller.surplus", args={"watts": 1200}),),
        origin=Origin.CONTROLLER,
    )
    return Decision(
        intent=intent,
        kind=DecisionKind.SHADOW,
        ts=NOW,
        reasons=(Reason(key="safety.shadow_mode"),),
        mode=OperatingMode.SHADOW,
    )


def make_entity(entity_id: str = "sensor.pv", **kw: Any) -> DetectedEntity:
    fields: dict[str, Any] = {
        "entity_id": entity_id,
        "name": "PV",
        "domain": "sensor",
        "platform": None,
        "device_class": "power",
        "unit": "W",
        "state_class": None,
        "area_id": None,
        "area_name": None,
        "device_id": None,
        "device_name": None,
        "score": 0,
        "criteria": (),
        "state": "1200",
        "available": True,
        "value": 1200.0,
        "quality": Quality.OK,
    }
    fields.update(kw)
    return DetectedEntity(**fields)


def make_flows(pv: float = 3000.0) -> FlowSnapshot:
    return FlowSnapshot(
        grid_w=Flow(-1000.0, Quality.OK),
        pv_w=Flow(pv, Quality.OK),
        battery_w=Flow(500.0, Quality.OK),
        house_w=Flow(1500.0, Quality.STALE),
        battery_soc_pct=Flow(None, Quality.UNAVAILABLE),
        ts=NOW,
    )


def make_device_state(device_id: str = "pool", *, held: bool = False) -> DeviceControlState:
    return DeviceControlState(
        device_id=device_id,
        name="Pool pump",
        kind=DeviceKind.SWITCH,
        identity="current",
        global_mode=OperatingMode.SHADOW,
        device_mode=OperatingMode.SHADOW,
        effective_mode=OperatingMode.SHADOW,
        state=DeviceState(on=False),
        estimate=PowerEstimate(value_w=0.0, uncertainty_w=10.0),
        phase="idle",
        level=0,
        top=1,
        expected_power_w=0.0,
        committed_w=0.0,
        base_setpoint_c=None,
        under_control=False,
        held=held,
        held_until=None,
        release_pending=False,
        release_due=None,
        paused=None,
        reasons=[],
        since=None,
        last_decision=None,
    )


def make_control_state(**over: Any) -> ControlState:
    fields: dict[str, Any] = {
        "ts": NOW,
        "active": True,
        "emergency_stop": False,
        "global_mode": OperatingMode.SHADOW,
        "site": None,
        "devices": [make_device_state()],
        "proposals": [],
    }
    fields.update(over)
    return ControlState(**fields)


class FakeServices:
    """In-memory ``Services`` with knobs for the tests."""

    version = "9.9.9"
    demo = False
    mode = "shadow"
    max_import_bytes = 200

    def __init__(self) -> None:
        self.peer_policy = PeerPolicy(enforce=False)
        self.static_dir: Path | None = None
        self.ha_state = "connected"
        self.db_ok = True
        self.site: SiteConfig | None = None
        self.sections: dict[str, BaseModel] = {}
        self.flow: FlowSnapshot | None = make_flows()
        self.decision_list: list[Decision] = [make_decision(1)]
        self.entity_list: list[DetectedEntity] = [make_entity()]
        self.queries: list[EntityQuery] = []
        self.catalog: TemplateCatalog = load_templates(TEMPLATES_DIR)
        self.history_entries: dict[str, list[HistoryEntry]] = {}
        self.restored: list[tuple[str, int]] = []
        self.energy = EnergySuggestions(configured=False)
        self.energy_failed = False
        self.check_items: list[CheckItem] = []
        self.checked_sites: list[SiteConfig | None] = []
        self.preview_items: list[PreviewItem] = []
        self.fragments: list[Mapping[str, Any]] = []
        self.ha_down = False
        self.imported: list[object] = []
        self.listeners: list[Callable[[], None]] = []
        self.health_listeners: list[Callable[[], None]] = []
        self.health_calls = 0
        self.last_limit: int | None = None
        self.decision_filters: list[tuple[str | None, tuple[DecisionKind, ...] | None]] = []
        self.control: ControlState = make_control_state()
        self.calls: list[tuple[str, object]] = []
        self.proposal_list: list[Decision] = []
        self.operation = OperationConfig()
        self.control_listeners: list[Callable[[], None]] = []
        self.pv_view = PvForecastView(generated_at=NOW, hours=48, sources=[], blended=None)
        self.accuracy = accuracy_view([], {})
        self.pv_hours: list[int] = []
        self.load_view = LoadForecastService(None, None, clock=lambda: NOW).view(48)
        self.load_hours: list[int] = []

    async def pv_forecast(self, hours: int) -> PvForecastView:
        self.pv_hours.append(hours)
        return self.pv_view

    async def forecast_accuracy(self) -> ForecastAccuracyView:
        return self.accuracy

    async def load_forecast(self, hours: int) -> LoadForecastView:
        self.load_hours.append(hours)
        return self.load_view

    async def health(self) -> HealthReport:
        self.health_calls += 1
        return HealthReport(self.ha_state, 1.5, self.db_ok, self.version)

    async def get_site(self) -> SiteConfig | None:
        return self.site

    async def put_site(self, site: SiteConfig) -> None:
        self.site = site

    async def get_section(self, name: str) -> BaseModel | None:
        return self.sections.get(name)

    async def put_section(self, name: str, model: BaseModel) -> BaseModel:
        self.sections[name] = model
        return model

    async def entities(self, query: EntityQuery) -> Sequence[DetectedEntity]:
        self.queries.append(query)
        if self.ha_down:
            raise HAUnavailableError("down")
        if query.role == "nope":
            raise UnknownRoleError("nope")
        if query.template_id == "nope.nope":
            raise UnknownTemplateError("nope.nope")
        return self.entity_list

    def templates(self) -> TemplateCatalog:
        return self.catalog

    async def section_history(self, name: str) -> list[HistoryEntry]:
        if name not in SECTION_NAMES:
            raise UnknownSectionError(name)
        return self.history_entries.get(name, [])

    async def restore_section(self, name: str, entry_id: int) -> BaseModel:
        if name not in SECTION_NAMES:
            raise UnknownSectionError(name)
        entry = next((e for e in self.history_entries.get(name, []) if e.id == entry_id), None)
        if entry is None:
            raise HistoryEntryNotFoundError(f"{name}/{entry_id}")
        self.restored.append((name, entry_id))
        model = SECTION_MODELS[name].model_validate({**entry.body, "schema_version": 1})
        self.sections[name] = model
        return model

    async def energy_suggestions(self) -> EnergySuggestions:
        if self.ha_down:
            raise HAUnavailableError("down")
        if self.energy_failed:
            raise EnergyPrefsFailedError("unauthorized")
        return self.energy

    async def system_check(self, site: SiteConfig | None) -> list[CheckItem]:
        self.checked_sites.append(site)
        return self.check_items

    async def preview(self, fragment: Mapping[str, Any]) -> list[PreviewItem]:
        self.fragments.append(fragment)
        if fragment.get("too") == "big":
            raise PreviewTooLargeError("too big")
        return self.preview_items

    def flows(self) -> FlowSnapshot | None:
        return self.flow

    async def decisions(
        self,
        limit: int,
        *,
        device_id: str | None = None,
        kinds: Collection[DecisionKind] | None = None,
    ) -> list[Decision]:
        self.last_limit = limit
        self.decision_filters.append((device_id, tuple(kinds) if kinds else None))
        found = [
            d
            for d in self.decision_list
            if (device_id is None or d.intent.asset_id == device_id)
            and (not kinds or d.kind in kinds)
        ]
        return found[:limit]

    # --- control -------------------------------------------------------------------------

    def control_state(self) -> ControlState:
        return self.control

    async def emergency_stop(self) -> None:
        self.control = self.control.model_copy(update={"emergency_stop": True})
        self.calls.append(("emergency_stop", None))

    async def clear_emergency_stop(self) -> None:
        self.control = self.control.model_copy(update={"emergency_stop": False})
        self.calls.append(("clear_emergency_stop", None))

    async def override_device(self, device_id: str, command: OverrideCommand) -> DeviceControlState:
        if device_id != "pool":
            raise UnknownDeviceError(device_id)
        if command.until is not None and command.until <= datetime(2026, 1, 1, tzinfo=UTC):
            raise OverrideUntilPastError(device_id)
        self.calls.append(("override", (device_id, command)))
        return make_device_state(device_id, held=command.action != "resume")

    def proposals(self) -> list[Decision]:
        return list(self.proposal_list)

    async def confirm_proposal(self, intent_id: str) -> Decision:
        found = next((d for d in self.proposal_list if d.intent.id == intent_id), None)
        if found is None:
            raise UnknownProposalError(intent_id)
        self.proposal_list.remove(found)
        self.calls.append(("confirm", intent_id))
        return found.model_copy(update={"kind": DecisionKind.ACCEPTED})

    async def dismiss_proposal(self, intent_id: str) -> None:
        found = next((d for d in self.proposal_list if d.intent.id == intent_id), None)
        if found is None:
            raise UnknownProposalError(intent_id)
        self.proposal_list.remove(found)
        self.calls.append(("dismiss", intent_id))

    async def set_global_mode(self, mode: OperatingMode) -> OperationConfig:
        self.operation = self.operation.model_copy(update={"mode": mode})
        return self.operation

    async def set_device_mode(
        self, device_id: str, mode: OperatingMode, identity: str | None
    ) -> OperationConfig:
        if device_id != "pool":
            raise UnknownDeviceError(device_id)
        if mode is not OperatingMode.SHADOW and identity != "current":
            raise DeviceIdentityChangedError(device_id)
        modes = {**self.operation.device_modes, device_id: mode}
        self.operation = self.operation.model_copy(update={"device_modes": modes})
        return self.operation

    def add_control_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        self.control_listeners.append(listener)

        def remove() -> None:
            with contextlib.suppress(ValueError):
                self.control_listeners.remove(listener)

        return remove

    def trigger_control(self) -> None:
        for listener in list(self.control_listeners):
            listener()

    async def export_config(self) -> dict[str, Any]:
        return {"suncore_export": 1, "sections": {}}

    async def import_config(self, data: object) -> list[str]:
        if not isinstance(data, dict) or data.get("suncore_export") != 1:
            raise ImportRejectedError("import: bad header")
        self.imported.append(data)
        return ["site"]

    def add_change_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        self.listeners.append(listener)

        def remove() -> None:
            with contextlib.suppress(ValueError):
                self.listeners.remove(listener)

        return remove

    def add_health_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        self.health_listeners.append(listener)

        def remove() -> None:
            with contextlib.suppress(ValueError):
                self.health_listeners.remove(listener)

        return remove

    def trigger_health(self) -> None:
        for listener in list(self.health_listeners):
            listener()

    def trigger(self) -> None:
        for listener in list(self.listeners):
            listener()


@pytest.fixture
def services() -> FakeServices:
    return FakeServices()


def client_for(app: FastAPI, client: tuple[str, int] = ("127.0.0.1", 50000)) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, client=client)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@contextlib.asynccontextmanager
async def serving(app: FastAPI) -> AsyncIterator[int]:
    """Run ``app`` on an ephemeral loopback port with real uvicorn; yield the port."""
    config = uvicorn.Config(
        app, host="127.0.0.1", port=0, log_config=None, lifespan="off", access_log=False
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    try:
        while not server.started:
            if task.done():
                task.result()
            await asyncio.sleep(0.01)
        sockets = server.servers[0].sockets
        yield int(sockets[0].getsockname()[1])
    finally:
        server.should_exit = True
        await task
