# SPDX-License-Identifier: Apache-2.0
"""Inert ``Services`` used to build the app object for OpenAPI export (no I/O, never serves)."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from suncore import __version__
from suncore.api.services import CandidateInfo, HealthReport, PeerPolicy
from suncore.core.balance import FlowSnapshot
from suncore.core.decisions import Decision
from suncore.core.site import SiteConfig


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

    async def candidates(self) -> Sequence[CandidateInfo]:
        return []

    def flows(self) -> FlowSnapshot | None:
        return None

    async def decisions(self, limit: int) -> list[Decision]:  # noqa: ARG002
        return []

    async def export_config(self) -> dict[str, Any]:
        return {}

    async def import_config(self, data: object) -> list[str]:  # noqa: ARG002
        return []

    def add_change_listener(self, listener: Callable[[], None]) -> Callable[[], None]:  # noqa: ARG002
        return lambda: None

    def add_health_listener(self, listener: Callable[[], None]) -> Callable[[], None]:  # noqa: ARG002
        return lambda: None
