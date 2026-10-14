# SPDX-License-Identifier: Apache-2.0
"""Inert ``Services`` used to build the app object for OpenAPI export (no I/O, never serves)."""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping, Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from suncore import __version__
from suncore.api.services import EntityQuery, HealthReport, PeerPolicy
from suncore.core.balance import FlowSnapshot
from suncore.core.config import OperationConfig
from suncore.core.controlstate import ControlState, DeviceControlState, OverrideCommand
from suncore.core.decisions import Decision, DecisionKind, OperatingMode
from suncore.core.detection import DetectedEntity, EnergySuggestions
from suncore.core.site import SiteConfig
from suncore.core.wizard import CheckItem, PreviewItem
from suncore.storage import HistoryEntry
from suncore.templates import TemplateCatalog


class StubServices:
    version = __version__
    demo = False
    mode = "shadow"
    max_import_bytes = 1
    peer_policy = PeerPolicy(enforce=False)
    static_dir: Path | None = None

    async def health(self) -> HealthReport:
        return HealthReport("disconnected", None, True, __version__)

    async def get_site(self) -> SiteConfig | None:
        return None

    async def put_site(self, site: SiteConfig) -> None: ...

    async def get_section(self, name: str) -> BaseModel | None:  # noqa: ARG002
        return None

    async def put_section(self, name: str, model: BaseModel) -> BaseModel:  # noqa: ARG002
        return model

    async def entities(self, query: EntityQuery) -> Sequence[DetectedEntity]:  # noqa: ARG002
        return []

    def templates(self) -> TemplateCatalog:
        return TemplateCatalog()

    async def section_history(self, name: str) -> list[HistoryEntry]:  # noqa: ARG002
        return []

    async def restore_section(self, name: str, entry_id: int) -> BaseModel:
        raise NotImplementedError

    async def energy_suggestions(self) -> EnergySuggestions:
        return EnergySuggestions(configured=False)

    async def system_check(self, site: SiteConfig | None) -> list[CheckItem]:  # noqa: ARG002
        return []

    async def preview(self, fragment: Mapping[str, Any]) -> list[PreviewItem]:  # noqa: ARG002
        return []

    def flows(self) -> FlowSnapshot | None:
        return None

    async def decisions(
        self,
        limit: int,  # noqa: ARG002
        *,
        device_id: str | None = None,  # noqa: ARG002
        kinds: Collection[DecisionKind] | None = None,  # noqa: ARG002
    ) -> list[Decision]:
        return []

    def control_state(self) -> ControlState:
        return ControlState(
            ts=None,
            active=False,
            emergency_stop=False,
            global_mode=OperatingMode.SHADOW,
            site=None,
            devices=[],
            proposals=[],
        )

    async def emergency_stop(self) -> None: ...

    async def clear_emergency_stop(self) -> None: ...

    async def override_device(
        self,
        device_id: str,
        command: OverrideCommand,
    ) -> DeviceControlState:
        raise NotImplementedError

    def proposals(self) -> list[Decision]:
        return []

    async def confirm_proposal(self, intent_id: str) -> Decision:
        raise NotImplementedError

    async def dismiss_proposal(self, intent_id: str) -> None: ...

    async def set_global_mode(self, mode: OperatingMode) -> OperationConfig:
        return OperationConfig(mode=mode)

    async def set_device_mode(
        self,
        device_id: str,  # noqa: ARG002
        mode: OperatingMode,  # noqa: ARG002
        identity: str | None,  # noqa: ARG002
    ) -> OperationConfig:
        return OperationConfig()

    def add_control_listener(self, listener: Callable[[], None]) -> Callable[[], None]:  # noqa: ARG002
        return lambda: None

    async def export_config(self) -> dict[str, Any]:
        return {}

    async def import_config(self, data: object) -> list[str]:  # noqa: ARG002
        return []

    def add_change_listener(self, listener: Callable[[], None]) -> Callable[[], None]:  # noqa: ARG002
        return lambda: None

    def add_health_listener(self, listener: Callable[[], None]) -> Callable[[], None]:  # noqa: ARG002
        return lambda: None
