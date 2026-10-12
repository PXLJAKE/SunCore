# SPDX-License-Identifier: Apache-2.0
"""FastAPI application factory.

All paths are relative-friendly for Home Assistant ingress: the proxy strips its prefix,
and the SPA addresses the API relatively (``api/v1/...``).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any, Final

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import TypeAdapter

from suncore.api.models import (
    DecisionsMessage,
    EntityCandidateOut,
    ErrorOut,
    FlowsMessage,
    FlowsOut,
    HealthMessage,
    HealthOut,
    ImportResultOut,
    InfoOut,
    LiveMessage,
    SnapshotMessage,
)
from suncore.api.security import PeerCheckMiddleware
from suncore.api.services import HAUnavailableError, Services
from suncore.core.decisions import Decision
from suncore.core.site import SiteConfig
from suncore.storage.errors import ImportRejectedError

log = logging.getLogger(__name__)

API_PREFIX: Final = "/api/v1"
DECISIONS_MAX: Final = 1000
SNAPSHOT_DECISIONS: Final = 20


def create_app(
    services: Services,
    *,
    live_min_interval_s: float = 0.5,
    live_health_interval_s: float = 5.0,
) -> FastAPI:
    """Build the API around ``services`` (real runtime or test fakes)."""
    app = FastAPI(
        title="SunCore API",
        version=services.version,
        docs_url=None,
        redoc_url=None,
    )
    app.add_middleware(PeerCheckMiddleware, policy=services.peer_policy)
    _register_http(app, services)
    _register_live(app, services, live_min_interval_s, live_health_interval_s)
    _mount_spa(app, services)
    app.openapi = lambda: _openapi(app)  # type: ignore[method-assign]
    return app


def _register_http(app: FastAPI, services: Services) -> None:
    @app.get("/health", response_model=HealthOut, responses={503: {"model": HealthOut}})
    async def health() -> Response:
        report = await services.health()
        body = HealthOut.from_report(report)
        return JSONResponse(body.model_dump(mode="json"), status_code=200 if report.ok else 503)

    @app.get(f"{API_PREFIX}/info", response_model=InfoOut)
    async def info() -> InfoOut:
        return InfoOut.model_validate(
            {"version": services.version, "demo": services.demo, "mode": services.mode}
        )

    @app.get(f"{API_PREFIX}/config/site", response_model=SiteConfig | None)
    async def get_site() -> SiteConfig | None:
        return await services.get_site()

    @app.put(f"{API_PREFIX}/config/site", response_model=SiteConfig)
    async def put_site(site: SiteConfig) -> SiteConfig:
        await services.put_site(site)
        return site

    @app.get(
        f"{API_PREFIX}/entities",
        response_model=list[EntityCandidateOut],
        responses={503: {"model": ErrorOut}},
    )
    async def entities() -> list[EntityCandidateOut]:
        try:
            found = await services.candidates()
        except HAUnavailableError as exc:
            raise HTTPException(status_code=503, detail="Home Assistant is not reachable") from exc
        return [EntityCandidateOut.from_info(c) for c in found]

    @app.get(f"{API_PREFIX}/config/export")
    async def export_config() -> dict[str, Any]:
        return await services.export_config()

    @app.post(
        f"{API_PREFIX}/config/import",
        response_model=ImportResultOut,
        responses={413: {"model": ErrorOut}, 422: {"model": ErrorOut}},
    )
    async def import_config(request: Request) -> ImportResultOut:
        limit = services.max_import_bytes
        declared = request.headers.get("content-length")
        if declared is not None and declared.isdigit() and int(declared) > limit:
            raise HTTPException(status_code=413, detail="import body too large")
        chunks: list[bytes] = []
        size = 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > limit:
                raise HTTPException(status_code=413, detail="import body too large")
            chunks.append(chunk)
        try:
            data = json.loads(b"".join(chunks))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=422, detail="import body is not valid JSON") from exc
        try:
            sections = await services.import_config(data)
        except ImportRejectedError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return ImportResultOut(imported_sections=sections)

    @app.get(f"{API_PREFIX}/decisions", response_model=list[Decision])
    async def decisions(
        limit: int = Query(default=100, ge=1, le=DECISIONS_MAX),
    ) -> list[Decision]:
        return await services.decisions(limit)


def _register_live(
    app: FastAPI, services: Services, live_min_interval_s: float, live_health_interval_s: float
) -> None:
    @app.websocket(f"{API_PREFIX}/live")
    async def live(ws: WebSocket) -> None:
        await ws.accept()
        changed = asyncio.Event()
        remove = services.add_change_listener(changed.set)
        pump = asyncio.create_task(
            _pump(ws, services, changed, live_min_interval_s, live_health_interval_s)
        )
        drain = asyncio.create_task(_drain(ws))
        try:
            await asyncio.wait({pump, drain}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            remove()
            for task in (pump, drain):
                task.cancel()
            await asyncio.gather(pump, drain, return_exceptions=True)
            with contextlib.suppress(RuntimeError, WebSocketDisconnect):
                await ws.close()


# --- live feed ------------------------------------------------------------------


async def _drain(ws: WebSocket) -> None:
    """Read and ignore client messages so a disconnect is noticed."""
    with contextlib.suppress(WebSocketDisconnect, RuntimeError):
        while True:
            await ws.receive_text()


def _flows_out(services: Services) -> FlowsOut | None:
    snap = services.flows()
    return None if snap is None else FlowsOut.from_core(snap)


async def _pump(
    ws: WebSocket,
    services: Services,
    changed: asyncio.Event,
    min_interval_s: float,
    health_interval_s: float,
) -> None:
    loop = asyncio.get_running_loop()
    try:
        health = await services.health()
        latest = await services.decisions(SNAPSHOT_DECISIONS)
        snapshot = SnapshotMessage(
            flows=_flows_out(services),
            health=HealthOut.from_report(health),
            decisions=latest,
        )
        await ws.send_json(snapshot.model_dump(mode="json"))
        last_key = (health.ha_state, health.db_ok)
        last_decisions = _decision_keys(latest)
        next_health = loop.time() + health_interval_s
        changed.clear()
        while True:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(changed.wait(), timeout=max(0.0, next_health - loop.time()))
            flows_changed = changed.is_set()
            changed.clear()
            if flows_changed:
                msg = FlowsMessage(flows=_flows_out(services))
                await ws.send_json(msg.model_dump(mode="json"))
            health = await services.health()
            due = loop.time() >= next_health
            if due or (health.ha_state, health.db_ok) != last_key:
                last_key = (health.ha_state, health.db_ok)
                next_health = loop.time() + health_interval_s
                hmsg = HealthMessage(health=HealthOut.from_report(health))
                await ws.send_json(hmsg.model_dump(mode="json"))
                latest = await services.decisions(SNAPSHOT_DECISIONS)
                keys = _decision_keys(latest)
                if keys != last_decisions:
                    last_decisions = keys
                    dmsg = DecisionsMessage(decisions=latest)
                    await ws.send_json(dmsg.model_dump(mode="json"))
            if flows_changed:
                await asyncio.sleep(min_interval_s)  # throttle: coalesce changes meanwhile
    except (WebSocketDisconnect, RuntimeError):
        return
    except Exception:
        log.exception("live feed failed")
        raise


def _decision_keys(decisions: list[Decision]) -> list[tuple[str, str, str]]:
    return [(d.ts.isoformat(), d.intent.id, d.kind.value) for d in decisions]


# --- static SPA -----------------------------------------------------------------


def _mount_spa(app: FastAPI, services: Services) -> None:
    root = services.static_dir
    if root is None:
        return
    root = root.resolve()
    index = root / "index.html"
    if not index.is_file():
        return

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str) -> Response:
        if path == "health" or path.startswith("api/") or path == "api":
            raise HTTPException(status_code=404, detail="not found")
        candidate = (root / path).resolve()
        if path and candidate.is_file() and candidate.is_relative_to(root):
            return FileResponse(candidate)
        return FileResponse(index)


# --- OpenAPI --------------------------------------------------------------------


def _openapi(app: FastAPI) -> dict[str, Any]:
    """OpenAPI plus the WebSocket message schemas (FastAPI cannot describe those)."""
    if app.openapi_schema is not None:
        return app.openapi_schema
    from fastapi.openapi.utils import get_openapi  # noqa: PLC0415

    schema = get_openapi(
        title=app.title,
        version=app.version,
        routes=app.routes,
        description="SunCore HTTP API. Live updates: WebSocket `api/v1/live` (see LiveMessage).",
    )
    live = TypeAdapter(LiveMessage).json_schema(ref_template="#/components/schemas/{model}")
    components = schema.setdefault("components", {}).setdefault("schemas", {})
    for name, definition in live.pop("$defs", {}).items():
        components.setdefault(name, definition)
    components["LiveMessage"] = live
    app.openapi_schema = schema
    return schema
