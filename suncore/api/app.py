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
import re
from typing import Annotated, Any, Final

from fastapi import (
    FastAPI,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, TypeAdapter
from starlette.websockets import WebSocketState

from suncore.api.limits import BodyLimitMiddleware
from suncore.api.models import (
    CheckOut,
    CheckRequest,
    ConfigHistoryEntryOut,
    ControlMessage,
    DecisionsMessage,
    DeviceModeRequest,
    EmergencyStopOut,
    EnergyPrefsOut,
    EntityCandidateOut,
    ErrorOut,
    FlowsMessage,
    FlowsOut,
    HealthMessage,
    HealthOut,
    HistoryRestoreOut,
    ImportResultOut,
    InfoOut,
    LiveMessage,
    ModeRequest,
    PreviewItemOut,
    PreviewOut,
    SnapshotMessage,
    TemplateDetailOut,
    TemplateListOut,
    ValidationErrorOut,
    template_list,
)
from suncore.api.security import PeerCheckMiddleware
from suncore.api.services import (
    EnergyPrefsFailedError,
    EntityQuery,
    HAUnavailableError,
    Services,
    UnknownRoleError,
    UnknownTemplateError,
)
from suncore.api.validation import validation_exception_handler
from suncore.core.config import (
    SECTION_MODELS,
    OperationConfig,
    TariffConfig,
    formula_on_all_in_source,
)
from suncore.core.controlstate import (
    ControlState,
    DeviceControlState,
    DeviceIdentityChangedError,
    ModeRaiseRejectedError,
    OverrideCommand,
    OverrideUntilPastError,
    UnknownDeviceError,
    UnknownProposalError,
    override_until_past,
)
from suncore.core.decisions import Decision, DecisionKind
from suncore.core.site import SiteConfig
from suncore.core.wizard import PreviewTooLargeError
from suncore.forecast.views import (
    MAX_VIEW_HOURS,
    ForecastAccuracyView,
    LoadForecastView,
    PvForecastView,
)
from suncore.storage.errors import (
    ConfigCorruptError,
    HistoryEntryNotFoundError,
    ImportRejectedError,
    UnknownSectionError,
)
from suncore.templates import TemplateCatalog
from suncore.templates.models import TariffTemplate, TemplateKind

log = logging.getLogger(__name__)

API_PREFIX: Final = "/api/v1"
DECISIONS_MAX: Final = 1000

# Stable error keys for ``detail``; the SPA renders them through the message catalogue.
ERR_HA_UNAVAILABLE: Final = "api.error.ha_unavailable"
ERR_IMPORT_TOO_LARGE: Final = "api.error.import_too_large"
ERR_INVALID_JSON: Final = "api.error.invalid_json"
ERR_IMPORT_REJECTED: Final = "api.error.import_rejected"
ERR_NOT_FOUND: Final = "api.error.not_found"
ERR_UNKNOWN_SECTION: Final = "api.error.unknown_section"
ERR_UNKNOWN_ROLE: Final = "api.error.unknown_role"
ERR_UNKNOWN_TEMPLATE: Final = "api.error.unknown_template"
ERR_ROLE_REQUIRED: Final = "api.error.role_required"
ERR_ENERGY_PREFS_FAILED: Final = "api.error.energy_prefs_failed"
ERR_HISTORY_NOT_FOUND: Final = "api.error.history_not_found"
ERR_HISTORY_INVALID: Final = "api.error.history_invalid"
ERR_PREVIEW_TOO_LARGE: Final = "api.error.preview_too_large"
ERR_BODY_TOO_LARGE: Final = "api.error.body_too_large"
ERR_UNKNOWN_DEVICE: Final = "api.error.unknown_device"
ERR_UNKNOWN_PROPOSAL: Final = "api.error.unknown_proposal"
ERR_DEVICE_IDENTITY_CHANGED: Final = "api.error.device_identity_changed"
ERR_MODE_RAISE_REJECTED: Final = "api.error.mode_raise_rejected"
BODY_MAX_BYTES: Final = 64 * 1024
"""Limit of the small JSON bodies of the wizard endpoints (check, preview)."""
SECTION_MAX_BYTES: Final = 256 * 1024
"""Limit of ``PUT /config/<section>``."""
BODY_LIMITS: Final = [
    ("PUT", re.compile(rf"{re.escape(API_PREFIX)}/config/[^/]+"), SECTION_MAX_BYTES),
    ("POST", re.compile(rf"{re.escape(API_PREFIX)}/wizard/check"), BODY_MAX_BYTES),
    ("POST", re.compile(rf"{re.escape(API_PREFIX)}/preview"), BODY_MAX_BYTES),
]
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
    app.add_middleware(BodyLimitMiddleware, rules=BODY_LIMITS)
    app.add_middleware(PeerCheckMiddleware, policy=services.peer_policy)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
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

    _register_sections(app, services)
    _register_history(app, services)
    _register_control(app, services)
    _register_forecast(app, services)

    @app.get(
        f"{API_PREFIX}/entities",
        response_model=list[EntityCandidateOut],
        responses={
            404: {"model": ErrorOut, "description": "Unknown role or template"},
            400: {"model": ErrorOut, "description": "Role missing"},
            503: {"model": ErrorOut, "description": "Home Assistant not reachable"},
        },
    )
    async def entities(
        role: str | None = Query(default=None, max_length=64),
        template: str | None = Query(default=None, max_length=96),
        device_id: str | None = Query(default=None, max_length=128),
        device_name: str | None = Query(default=None, max_length=128),
        include_hidden: bool = False,
    ) -> list[EntityCandidateOut]:
        """Entities for a site role (``role``) or a template role (``template`` + ``role``).

        Without parameters: the unranked power and battery sensors of the Phase 1 picker.
        ``include_hidden`` also lists hidden, diagnostic and config entities (ranked lower);
        disabled entities never appear. ``device_name`` (template roles) ranks candidates of a
        device with that name first. Served from the registry snapshot and state cache,
        never per-request from HA.
        """
        if template is not None and role is None:
            raise HTTPException(status_code=400, detail=ERR_ROLE_REQUIRED)
        try:
            found = await services.entities(
                EntityQuery(
                    role=role,
                    template_id=template,
                    device_id=device_id,
                    device_name=device_name,
                    include_hidden=include_hidden,
                )
            )
        except HAUnavailableError as exc:
            raise HTTPException(status_code=503, detail=ERR_HA_UNAVAILABLE) from exc
        except UnknownRoleError as exc:
            raise HTTPException(status_code=404, detail=ERR_UNKNOWN_ROLE) from exc
        except UnknownTemplateError as exc:
            raise HTTPException(status_code=404, detail=ERR_UNKNOWN_TEMPLATE) from exc
        return [EntityCandidateOut.from_detected(c) for c in found]

    _register_templates(app, services)
    _register_wizard(app, services)

    @app.get(f"{API_PREFIX}/config/export")
    async def export_config() -> dict[str, Any]:
        return await services.export_config()

    @app.post(
        f"{API_PREFIX}/config/import",
        response_model=ImportResultOut,
        # Explicit descriptions: the defaults come from http.HTTPStatus, whose
        # phrases differ between Python 3.12 and 3.13 (OpenAPI drift guard).
        responses={
            413: {"model": ErrorOut, "description": "Import too large"},
            422: {"model": ErrorOut, "description": "Import rejected"},
        },
    )
    async def import_config(request: Request) -> ImportResultOut:
        limit = services.max_import_bytes
        declared = request.headers.get("content-length")
        if declared is not None and declared.isdigit() and int(declared) > limit:
            raise HTTPException(status_code=413, detail=ERR_IMPORT_TOO_LARGE)
        chunks: list[bytes] = []
        size = 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > limit:
                raise HTTPException(status_code=413, detail=ERR_IMPORT_TOO_LARGE)
            chunks.append(chunk)
        try:
            data = json.loads(b"".join(chunks))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=422, detail=ERR_INVALID_JSON) from exc
        try:
            sections = await services.import_config(data)
        except ImportRejectedError as exc:
            log.info("config import rejected: %s", exc)  # reason stays in the log
            raise HTTPException(status_code=422, detail=ERR_IMPORT_REJECTED) from exc
        return ImportResultOut(imported_sections=sections)

    @app.get(f"{API_PREFIX}/decisions", response_model=list[Decision])
    async def decisions(
        limit: int = Query(default=100, ge=1, le=DECISIONS_MAX),
        device_id: str | None = Query(default=None, max_length=128),
        kind: Annotated[list[DecisionKind] | None, Query()] = None,
    ) -> list[Decision]:
        """The decision log, newest first. ``device_id`` keeps one device's decisions, ``kind``
        (repeatable) the given kinds (``shadow``, ``accepted``, ``proposed``, ...)."""
        if device_id is None and not kind:
            return await services.decisions(limit)
        return await services.decisions(limit, device_id=device_id, kinds=kind)


def _register_forecast(app: FastAPI, services: Services) -> None:
    """PV and load forecasts and their measured accuracy (Phase 4 WP5/WP6; read only, no UI
    yet)."""

    @app.get(f"{API_PREFIX}/forecast/pv", response_model=PvForecastView)
    async def forecast_pv(
        hours: int = Query(default=48, ge=1, le=MAX_VIEW_HOURS),
    ) -> PvForecastView:
        """Per source the latest good P10/P50/P90 in W from the current quarter hour on, with
        its freshness, and ``blended``: the forecast the planner uses (sources bias-calibrated
        where active, blended by inverse 14-day MAE), its weights per horizon bucket, the
        calibration state per source, and the blend without and with every learnt
        calibration for comparison."""
        return await services.pv_forecast(hours)

    @app.get(f"{API_PREFIX}/forecast/accuracy", response_model=ForecastAccuracyView)
    async def forecast_accuracy() -> ForecastAccuracyView:
        """MAE and bias per source and horizon bucket (0-6 h, 6-24 h, 24-48 h) over the last 14
        days, plus the source id ``blended`` (the blended forecast); ``bias_w = actual -
        forecast`` (positive: the source under-forecasts); ``n`` counts 15-minute intervals;
        clipped quarter hours are excluded; computed nightly. The source id ``load_profile`` is
        the base load forecast (scored on every quarter hour, not only in daylight)."""
        return await services.forecast_accuracy()

    @app.get(f"{API_PREFIX}/forecast/load", response_model=LoadForecastView)
    async def forecast_load(
        hours: int = Query(default=48, ge=1, le=MAX_VIEW_HOURS),
    ) -> LoadForecastView:
        """The base load forecast (house consumption without the configured devices; W,
        P10/P50/P90 on the 15-minute grid from the current quarter hour), learnt from the
        history per day type (workday/weekend/holiday) with an optional temperature term; its
        quality (``ok``, ``cold_start``, ``no_history``), the day type of every forecast date
        and where it came from, and how many quarter hours lacked a device measurement."""
        return await services.load_forecast(hours)


def _register_control(app: FastAPI, services: Services) -> None:
    """Control state, emergency stop, overrides, proposals and operating modes (WP9)."""

    @app.get(f"{API_PREFIX}/control/state", response_model=ControlState)
    async def control_state() -> ControlState:
        """Per device: observed state, power estimate with uncertainty, effective mode, hold and
        release state, the controller's reasons and the newest gate decision; for the site the
        smoothed total and free surplus and the deficit; the open proposals."""
        return services.control_state()

    @app.post(f"{API_PREFIX}/control/emergency-stop", response_model=EmergencyStopOut)
    async def emergency_stop() -> EmergencyStopOut:
        """Release every device (baseline restored) and refuse all writes until cleared."""
        await services.emergency_stop()
        return EmergencyStopOut(active=True)

    @app.post(f"{API_PREFIX}/control/emergency-stop/clear", response_model=EmergencyStopOut)
    async def clear_emergency_stop() -> EmergencyStopOut:
        """Allow control again; the operating modes are unchanged."""
        await services.clear_emergency_stop()
        return EmergencyStopOut(active=False)

    @app.post(
        f"{API_PREFIX}/devices/{{device_id}}/override",
        response_model=DeviceControlState,
        responses={404: {"model": ErrorOut, "description": "Unknown device"}},
    )
    async def override(device_id: str, body: OverrideCommand) -> DeviceControlState:
        """Boost (N minutes), pause (until a time or until resumed) or resume one device."""
        try:
            return await services.override_device(device_id, body)
        except UnknownDeviceError as exc:
            raise HTTPException(status_code=404, detail=ERR_UNKNOWN_DEVICE) from exc
        except OverrideUntilPastError as exc:
            # Same 422 body as a request validation error: only the service knows the clock.
            error = override_until_past()
            raise RequestValidationError(
                [{"type": error.type, "loc": ("body", "until"), "msg": error.message()}]
            ) from exc

    @app.get(f"{API_PREFIX}/proposals", response_model=list[Decision])
    async def proposals() -> list[Decision]:
        """Open assisted-mode proposals, oldest first (valid until ``expires_at``)."""
        return services.proposals()

    @app.post(
        f"{API_PREFIX}/proposals/{{intent_id}}/confirm",
        response_model=Decision,
        responses={404: {"model": ErrorOut, "description": "Unknown or expired proposal"}},
    )
    async def confirm_proposal(intent_id: str) -> Decision:
        """Confirm: the proposal is submitted as the user's intent; the answer is the gate's
        decision (``accepted``, ``rejected`` by an invariant, ``failed`` ...)."""
        try:
            return await services.confirm_proposal(intent_id)
        except UnknownProposalError as exc:
            raise HTTPException(status_code=404, detail=ERR_UNKNOWN_PROPOSAL) from exc

    @app.post(
        f"{API_PREFIX}/proposals/{{intent_id}}/dismiss",
        status_code=204,
        responses={404: {"model": ErrorOut, "description": "Unknown or expired proposal"}},
    )
    async def dismiss_proposal(intent_id: str) -> Response:
        try:
            await services.dismiss_proposal(intent_id)
        except UnknownProposalError as exc:
            raise HTTPException(status_code=404, detail=ERR_UNKNOWN_PROPOSAL) from exc
        return Response(status_code=204)

    @app.put(f"{API_PREFIX}/operation/mode", response_model=OperationConfig)
    async def set_global_mode(body: ModeRequest) -> OperationConfig:
        """Set the global operating mode. A device runs in ``min(global, device)``; lowering to
        shadow releases the controlled devices (after their minimum run or pause time)."""
        return await services.set_global_mode(body.mode)

    @app.put(
        f"{API_PREFIX}/operation/devices/{{device_id}}",
        response_model=OperationConfig,
        responses={
            404: {"model": ErrorOut, "description": "Unknown device"},
            409: {
                "model": ErrorOut,
                "description": "The device changed since its identity was read",
            },
        },
    )
    async def set_device_mode(device_id: str, body: DeviceModeRequest) -> OperationConfig:
        """Set one device's mode. Anything above shadow needs the device's ``identity`` token
        from the control state; shadow never does."""
        try:
            return await services.set_device_mode(device_id, body.mode, body.identity)
        except UnknownDeviceError as exc:
            raise HTTPException(status_code=404, detail=ERR_UNKNOWN_DEVICE) from exc
        except DeviceIdentityChangedError as exc:
            raise HTTPException(status_code=409, detail=ERR_DEVICE_IDENTITY_CHANGED) from exc


def _register_sections(app: FastAPI, services: Services) -> None:
    """``GET``/``PUT /api/v1/config/<section>`` for every section, typed by its model.

    One pair of routes per section (instead of a ``{section}`` path parameter) so that the
    OpenAPI document and the generated client carry the exact model of each section.
    """
    for name, model in SECTION_MODELS.items():
        _register_section(app, services, name, model)


def _register_section(app: FastAPI, services: Services, name: str, model: type[BaseModel]) -> None:
    path = f"{API_PREFIX}/config/{name}"

    async def get_section() -> BaseModel | None:
        if name == "site":
            return await services.get_site()
        return await services.get_section(name)

    async def put_section(body: BaseModel) -> BaseModel:
        if name == "site":
            assert isinstance(body, SiteConfig)
            await services.put_site(body)
            return body
        if isinstance(body, TariffConfig):
            _check_tariff_against_catalog(body, services.templates())
        try:
            return await services.put_section(name, body)
        except ModeRaiseRejectedError as exc:
            raise HTTPException(status_code=409, detail=ERR_MODE_RAISE_REJECTED) from exc

    # FastAPI reads the body type from the annotations; they are per section, hence dynamic.
    put_section.__annotations__ = {"body": model, "return": model}
    app.add_api_route(
        path,
        get_section,
        methods=["GET"],
        response_model=model | None,
        operation_id=f"get_config_{name}",
        summary=f"Get the {name} section",
    )
    app.add_api_route(
        path,
        put_section,
        methods=["PUT"],
        response_model=model,
        responses={
            413: {"model": ErrorOut, "description": "Body too large"},
            **(
                {409: {"model": ErrorOut, "description": "The document would raise a mode"}}
                if name == "operation"
                else {}
            ),
        },
        operation_id=f"put_config_{name}",
        summary=f"Replace the {name} section",
    )


def _check_tariff_against_catalog(tariff: TariffConfig, catalog: TemplateCatalog) -> None:
    """Checks a tariff needs the template catalogue for (same 422 body as model validation)."""
    template_id = tariff.import_.template_id
    template = catalog.get(template_id) if template_id is not None else None
    includes = template.price_includes if isinstance(template, TariffTemplate) else None
    error = formula_on_all_in_source(tariff, includes)
    if error is not None:
        raise RequestValidationError(
            [
                {
                    "type": error.type,
                    "loc": ("body", "import", "formula"),
                    "msg": error.message(),
                    "ctx": error.context or {},
                }
            ]
        )


def _history_error(exc: Exception) -> HTTPException:
    if isinstance(exc, UnknownSectionError):
        return HTTPException(status_code=404, detail=ERR_UNKNOWN_SECTION)
    if isinstance(exc, HistoryEntryNotFoundError):
        return HTTPException(status_code=404, detail=ERR_HISTORY_NOT_FOUND)
    return HTTPException(status_code=409, detail=ERR_HISTORY_INVALID)


def _register_history(app: FastAPI, services: Services) -> None:
    @app.get(
        f"{API_PREFIX}/config/{{section}}/history",
        response_model=list[ConfigHistoryEntryOut],
        responses={404: {"model": ErrorOut, "description": "Unknown section"}},
    )
    async def history(section: str) -> list[ConfigHistoryEntryOut]:
        """Replaced bodies of a section, newest first (the last 20)."""
        try:
            entries = await services.section_history(section)
        except UnknownSectionError as exc:
            raise _history_error(exc) from exc
        return [ConfigHistoryEntryOut.from_entry(e) for e in entries]

    @app.post(
        f"{API_PREFIX}/config/{{section}}/history/{{entry_id}}/restore",
        response_model=HistoryRestoreOut,
        responses={
            404: {"model": ErrorOut, "description": "Unknown section or history entry"},
            409: {"model": ErrorOut, "description": "The stored body no longer validates"},
        },
    )
    async def restore(section: str, entry_id: int) -> HistoryRestoreOut:
        """Make a history entry current again (the current body moves to the history)."""
        try:
            restored = await services.restore_section(section, entry_id)
        except (UnknownSectionError, HistoryEntryNotFoundError, ConfigCorruptError) as exc:
            raise _history_error(exc) from exc
        return HistoryRestoreOut(
            section=section, body=restored.model_dump(mode="json", by_alias=True)
        )


def _register_templates(app: FastAPI, services: Services) -> None:
    @app.get(f"{API_PREFIX}/templates", response_model=TemplateListOut)
    async def templates(kind: TemplateKind | None = None) -> TemplateListOut:
        """Template gallery; ``errors`` lists user templates that failed to load."""
        return template_list(services.templates(), kind)

    @app.get(
        f"{API_PREFIX}/templates/{{template_id}}",
        response_model=TemplateDetailOut,
        responses={404: {"model": ErrorOut, "description": "Unknown template"}},
    )
    async def template(template_id: str) -> TemplateDetailOut:
        catalog = services.templates()
        found = catalog.get(template_id)
        if found is None:
            raise HTTPException(status_code=404, detail=ERR_UNKNOWN_TEMPLATE)
        return TemplateDetailOut(origin=catalog.origins.get(template_id, "builtin"), template=found)


def _register_wizard(app: FastAPI, services: Services) -> None:
    @app.get(
        f"{API_PREFIX}/wizard/energy-prefs",
        response_model=EnergyPrefsOut,
        responses={
            502: {"model": ErrorOut, "description": "Home Assistant refused the request"},
            503: {"model": ErrorOut, "description": "Home Assistant not reachable"},
        },
    )
    async def energy_prefs() -> EnergyPrefsOut:
        """Suggestions from the Energy dashboard; ``configured: false`` if it was never saved."""
        try:
            return EnergyPrefsOut.from_core(await services.energy_suggestions())
        except HAUnavailableError as exc:
            raise HTTPException(status_code=503, detail=ERR_HA_UNAVAILABLE) from exc
        except EnergyPrefsFailedError as exc:
            raise HTTPException(status_code=502, detail=ERR_ENERGY_PREFS_FAILED) from exc

    @app.post(
        f"{API_PREFIX}/wizard/check",
        response_model=CheckOut,
        responses={413: {"model": ErrorOut, "description": "Body too large"}},
    )
    async def check(body: CheckRequest | None = None) -> CheckOut:
        """System check of the stored site (or of ``site`` in the body) against live values."""
        site = body.site if body is not None else None
        return CheckOut.from_core(await services.system_check(site))

    @app.post(
        f"{API_PREFIX}/preview",
        response_model=PreviewOut,
        responses={
            413: {"model": ErrorOut, "description": "Body too large"},
            422: {"model": ErrorOut, "description": "Fragment names too many entities"},
        },
    )
    async def preview(fragment: dict[str, Any]) -> PreviewOut:
        """Live values of every entity in an unsaved SiteConfig or DeviceConfig fragment."""
        try:
            items = await services.preview(fragment)
        except PreviewTooLargeError as exc:
            raise HTTPException(status_code=422, detail=ERR_PREVIEW_TOO_LARGE) from exc
        return PreviewOut(items=[PreviewItemOut.from_core(i) for i in items])


def _register_live(
    app: FastAPI, services: Services, live_min_interval_s: float, live_health_interval_s: float
) -> None:
    @app.websocket(f"{API_PREFIX}/live")
    async def live(ws: WebSocket) -> None:
        await ws.accept()
        wake = _Wake()
        removers = (
            services.add_change_listener(wake.flows),
            services.add_health_listener(wake.health),
            services.add_control_listener(wake.control),
        )
        pump = asyncio.create_task(
            _pump(ws, services, wake, live_min_interval_s, live_health_interval_s)
        )
        drain = asyncio.create_task(_drain(ws))
        try:
            await asyncio.wait({pump, drain}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for remove in removers:
                remove()
            for task in (pump, drain):
                task.cancel()
            await asyncio.gather(pump, drain, return_exceptions=True)
            await _close_quietly(ws)


# --- live feed ------------------------------------------------------------------


class _Wake:
    """Wake-up signal of the pump: flow data changed and/or the connection state did."""

    def __init__(self) -> None:
        self.event = asyncio.Event()
        self.flows_changed = False
        self.health_changed = False
        self.control_changed = False

    def flows(self) -> None:
        self.flows_changed = True
        self.event.set()

    def health(self) -> None:
        self.health_changed = True
        self.event.set()

    def control(self) -> None:
        self.control_changed = True
        self.event.set()

    def take(self) -> tuple[bool, bool, bool]:
        taken = (self.flows_changed, self.health_changed, self.control_changed)
        self.flows_changed = self.health_changed = self.control_changed = False
        self.event.clear()
        return taken


def _client_gone(ws: WebSocket, exc: RuntimeError) -> bool:
    """A ``RuntimeError`` is a disconnect only if the client side is really gone."""
    if ws.client_state is WebSocketState.DISCONNECTED:
        return True
    log.error("live websocket failed: %r", exc)
    return False


async def _close_quietly(ws: WebSocket) -> None:
    try:
        await ws.close()
    except WebSocketDisconnect:
        pass
    except RuntimeError as exc:
        if ws.application_state is not WebSocketState.DISCONNECTED:
            _client_gone(ws, exc)  # logs unless the client has gone


async def _drain(ws: WebSocket) -> None:
    """Read and ignore client messages so a disconnect is noticed."""
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        return
    except RuntimeError as exc:
        _client_gone(ws, exc)  # either way the feed ends; the handler closes the socket


def _flows_out(services: Services) -> FlowsOut | None:
    snap = services.flows()
    return None if snap is None else FlowsOut.from_core(snap)


async def _pump(
    ws: WebSocket,
    services: Services,
    wake: _Wake,
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
            control=services.control_state(),
        )
        await ws.send_json(snapshot.model_dump(mode="json"))
        last_decisions = _decision_keys(latest)
        next_health = loop.time() + health_interval_s
        wake.take()
        while True:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    wake.event.wait(), timeout=max(0.0, next_health - loop.time())
                )
            flows_changed, state_changed, control_changed = wake.take()
            if flows_changed:
                msg = FlowsMessage(flows=_flows_out(services))
                await ws.send_json(msg.model_dump(mode="json"))
            if control_changed:
                cmsg = ControlMessage(control=services.control_state())
                await ws.send_json(cmsg.model_dump(mode="json"))
            # Health (with its DB check) runs on its own cadence or on a connection state
            # change, never per flow or control event.
            check_decisions = control_changed
            if state_changed or loop.time() >= next_health:
                health = await services.health()
                next_health = loop.time() + health_interval_s
                hmsg = HealthMessage(health=HealthOut.from_report(health))
                await ws.send_json(hmsg.model_dump(mode="json"))
                check_decisions = True
            if check_decisions:
                latest = await services.decisions(SNAPSHOT_DECISIONS)
                keys = _decision_keys(latest)
                if keys != last_decisions:
                    last_decisions = keys
                    dmsg = DecisionsMessage(decisions=latest)
                    await ws.send_json(dmsg.model_dump(mode="json"))
            if flows_changed or control_changed:
                await asyncio.sleep(min_interval_s)  # throttle: coalesce changes meanwhile
    except WebSocketDisconnect:
        return
    except RuntimeError as exc:
        if _client_gone(ws, exc):
            return
        raise
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
            raise HTTPException(status_code=404, detail=ERR_NOT_FOUND)
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
    _use_validation_error_schema(schema)
    app.openapi_schema = schema
    return schema


def _use_validation_error_schema(schema: dict[str, Any]) -> None:
    """Describe automatic 422 responses with our body instead of FastAPI's default one."""
    components = schema["components"]["schemas"]
    ours = TypeAdapter(ValidationErrorOut).json_schema(ref_template="#/components/schemas/{model}")
    for name, definition in ours.pop("$defs", {}).items():
        components.setdefault(name, definition)
    components["ValidationErrorOut"] = ours
    default_ref = "#/components/schemas/HTTPValidationError"
    for methods in schema["paths"].values():
        for operation in methods.values():
            response = operation.get("responses", {}).get("422")
            if response is None:
                continue
            content = response.get("content", {}).get("application/json", {})
            if content.get("schema", {}).get("$ref") == default_ref:
                content["schema"] = {"$ref": "#/components/schemas/ValidationErrorOut"}
                response["description"] = "Validation error"
    components.pop("HTTPValidationError", None)
    components.pop("ValidationError", None)
