# SPDX-License-Identifier: Apache-2.0
"""EV mode and target, program jobs and grid status for the panel (Phase 6, PR 6-M).

Every route only changes what the controller and the gate already read: the runtime EV state
(``flex_state``) and the program job book. Nothing here writes to a device; the safety gate
judges every device write that follows from it. Errors are catalogue keys in ``detail``.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, Final

from fastapi import FastAPI, HTTPException
from pydantic import AwareDatetime, BaseModel, ConfigDict, model_validator

from suncore.api.flex_types import EvTargetError, FlexStoreError, NotAnEvError
from suncore.api.models import ErrorOut
from suncore.core.controlstate import UnknownDeviceError
from suncore.core.flex import EvMode, EvState, EvTarget, ProgramJob, ProgramJobError
from suncore.core.validation import invalid

if TYPE_CHECKING:
    from suncore.api.services import Services

API_PREFIX: Final = "/api/v1"
ERR_UNKNOWN_DEVICE: Final = "api.error.unknown_device"
ERR_NOT_AN_EV: Final = "api.error.ev_not_an_ev"
ERR_STORE_FAILED: Final = "api.error.flex_store_failed"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FlexOut(_Model):
    """The runtime EV state and the open program job of one device."""

    device_id: str
    ev: EvState | None = None
    job: ProgramJob | None = None


class EvStateRequest(_Model):
    mode: EvMode
    target: EvTarget | None = None

    @model_validator(mode="after")
    def _target_for_target_mode(self) -> EvStateRequest:
        if self.mode is EvMode.TARGET and self.target is None:
            raise invalid("ev_mode_target_missing", "mode `target` needs a target")
        return self


class ProgramArmRequest(_Model):
    deadline: AwareDatetime
    """"Finished by": the program must be done at this time."""


class GridStatusOut(_Model):
    state: str
    para14a: str
    month: str
    month_peak_w: float | None
    peak_limit_w: float | None


class GridEventOut(_Model):
    ts: datetime
    kind: str
    value_w: float | None


class MonthPeakOut(_Model):
    month: str
    peak_w: float
    slot: datetime


class GridEventsOut(_Model):
    events: list[GridEventOut]
    peaks: list[MonthPeakOut]


_UNKNOWN: Final[dict[int | str, dict[str, Any]]] = {
    404: {"model": ErrorOut, "description": "Unknown device"}
}
_PROGRAM_ERRORS: Final[dict[int | str, dict[str, Any]]] = {
    404: {"model": ErrorOut, "description": "Unknown device"},
    409: {"model": ErrorOut, "description": "A job is open already, none is open or it runs"},
    422: {"model": ErrorOut, "description": "Not a program or the deadline is out of range"},
    503: {"model": ErrorOut, "description": "The job could not be stored"},
}
_PROGRAM_STATUS: Final = {
    "api.error.program_not_a_program": 422,
    "api.error.program_deadline_past": 422,
    "api.error.program_deadline_far": 422,
    ERR_STORE_FAILED: 503,
    "api.error.program_job_open": 409,
    "api.error.program_no_job": 409,
    "api.error.program_job_running": 409,
}


def register_flex(app: FastAPI, services: Services) -> None:
    """The EV, program and grid routes of the panel."""

    @app.get(
        f"{API_PREFIX}/devices/{{device_id}}/flex",
        response_model=FlexOut,
        responses=_UNKNOWN,
        operation_id="device_flex",
    )
    async def device_flex(device_id: str) -> FlexOut:
        """The device's runtime EV mode and target and its open program job (or ``null``)."""
        try:
            ev, job = await services.flex_view(device_id)
        except UnknownDeviceError as exc:
            raise HTTPException(status_code=404, detail=ERR_UNKNOWN_DEVICE) from exc
        return FlexOut(device_id=device_id, ev=ev, job=job)

    @app.put(
        f"{API_PREFIX}/devices/{{device_id}}/ev",
        response_model=EvState,
        responses={
            **_UNKNOWN,
            422: {
                "model": ErrorOut,
                "description": "Not an EV charger or the target time is out of range",
            },
            503: {"model": ErrorOut, "description": "The state could not be stored"},
        },
        operation_id="device_ev_put",
    )
    async def put_ev(device_id: str, body: EvStateRequest) -> EvState:
        """Set the charge mode (``off``, ``now``, ``min_pv``, ``pv``, ``target``) and, for
        ``target``, the SoC or energy to reach by a time. The controller follows it within a
        tick; the safety gate still judges every write."""
        try:
            return await services.set_ev_state(device_id, body.mode, body.target)
        except UnknownDeviceError as exc:
            raise HTTPException(status_code=404, detail=ERR_UNKNOWN_DEVICE) from exc
        except NotAnEvError as exc:
            raise HTTPException(status_code=422, detail=ERR_NOT_AN_EV) from exc
        except EvTargetError as exc:
            raise HTTPException(status_code=422, detail=exc.key) from exc
        except FlexStoreError as exc:
            raise HTTPException(status_code=503, detail=ERR_STORE_FAILED) from exc

    @app.post(
        f"{API_PREFIX}/devices/{{device_id}}/program/arm",
        response_model=ProgramJob,
        responses=_PROGRAM_ERRORS,
        operation_id="device_program_arm",
    )
    async def arm_program(device_id: str, body: ProgramArmRequest) -> ProgramJob:
        """Arm one job "finished by ``deadline``". SunCore schedules the start; a smart plug is
        switched off right after arming while the appliance is idle."""
        try:
            return await services.arm_program(device_id, body.deadline)
        except UnknownDeviceError as exc:
            raise HTTPException(status_code=404, detail=ERR_UNKNOWN_DEVICE) from exc
        except ProgramJobError as exc:
            status = _PROGRAM_STATUS.get(exc.key, 409)
            raise HTTPException(status_code=status, detail=exc.key) from exc

    @app.post(
        f"{API_PREFIX}/devices/{{device_id}}/program/cancel",
        response_model=ProgramJob,
        responses=_PROGRAM_ERRORS,
        operation_id="device_program_cancel",
    )
    async def cancel_program(device_id: str) -> ProgramJob:
        """Cancel the waiting job. SunCore lets go of the device: a smart plug is switched
        **on**, so the appliance starts at once. A running job cannot be cancelled."""
        try:
            return await services.cancel_program(device_id)
        except UnknownDeviceError as exc:
            raise HTTPException(status_code=404, detail=ERR_UNKNOWN_DEVICE) from exc
        except ProgramJobError as exc:
            status = _PROGRAM_STATUS.get(exc.key, 409)
            raise HTTPException(status_code=status, detail=exc.key) from exc

    @app.get(
        f"{API_PREFIX}/grid/status",
        response_model=GridStatusOut,
        operation_id="grid_status",
    )
    async def grid_status() -> GridStatusOut:
        """Island, §14a and the month's peak as the control stack sees them."""
        status = await services.grid_status()
        return GridStatusOut(
            state=status.state,
            para14a=status.para14a,
            month=status.month,
            month_peak_w=status.month_peak_w,
            peak_limit_w=status.peak_limit_w,
        )

    @app.get(
        f"{API_PREFIX}/grid/events",
        response_model=GridEventsOut,
        operation_id="grid_events",
    )
    async def grid_events(days: int = 30) -> GridEventsOut:
        """Grid events (island, §14a, new peak, import above the fuse limit) of the last
        ``days`` (1-400) and the monthly peaks."""
        view = await services.grid_events(max(1, min(days, 400)))
        return GridEventsOut(
            events=[GridEventOut(ts=e.ts, kind=e.kind, value_w=e.value_w) for e in view.events],
            peaks=[MonthPeakOut(month=p.month, peak_w=p.peak_w, slot=p.slot) for p in view.peaks],
        )


__all__ = ["EvStateRequest", "FlexOut", "ProgramArmRequest", "register_flex"]
