# SPDX-License-Identifier: Apache-2.0
"""``/api/v1/backtests``: start, list, read and cancel backtest runs (Phase 5 WP6).

A run replays stored history through the planner with an overlay (``suncore.sim.backtest``); it
never writes configuration or devices. One run at a time: a second start is a 409.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Final, Literal

from fastapi import FastAPI, HTTPException, Path
from pydantic import BaseModel, ConfigDict

from suncore.api.models import ErrorOut
from suncore.api.services import BacktestService, Services
from suncore.core.decisions import Reason
from suncore.sim.backtest_models import (
    OVERLAY_INVALID,
    OVERLAY_NO_BATTERY,
    BacktestBusyError,
    BacktestJobView,
    BacktestOverlayError,
    BacktestRequest,
    BacktestResult,
    BacktestStatus,
    BacktestUnavailableError,
    BacktestWindowError,
)

API_PREFIX: Final = "/api/v1"
ERR_BACKTEST_UNAVAILABLE: Final = "api.error.backtest_unavailable"
ERR_BACKTEST_BUSY: Final = "api.error.backtest_busy"
ERR_BACKTEST_NOT_FOUND: Final = "api.error.backtest_not_found"
ERR_BACKTEST_WINDOW: Final = "api.error.backtest_window_not_past"
OVERLAY_ERRORS: Final = {
    OVERLAY_INVALID: "api.error.backtest_overlay_invalid",
    OVERLAY_NO_BATTERY: "api.error.backtest_overlay_no_battery",
}

RunId = Annotated[int, Path(ge=1)]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BacktestProgressOut(_Model):
    done: int
    total: int


class BacktestRunOut(_Model):
    """One run; ``result`` only from ``GET /backtests/{id}`` of a finished run."""

    id: int
    status: BacktestStatus
    created_at: datetime
    finished_at: datetime | None
    start: date
    end: date
    request: BacktestRequest | None
    progress: BacktestProgressOut | None
    result: BacktestResult | None
    error: Reason | None


class BacktestCancelOut(_Model):
    outcome: Literal["cancelled", "deleted"]


def _out(view: BacktestJobView, *, with_result: bool) -> BacktestRunOut:
    progress = None
    if view.progress_done is not None and view.progress_total is not None:
        progress = BacktestProgressOut(done=view.progress_done, total=view.progress_total)
    return BacktestRunOut(
        id=view.id,
        status=view.status,
        created_at=view.created_at,
        finished_at=view.finished_at,
        start=view.start,
        end=view.end,
        request=view.request,
        progress=progress,
        result=view.result if with_result else None,
        error=view.error,
    )


def _jobs(services: Services) -> BacktestService:
    jobs = services.backtests()
    if jobs is None:
        raise HTTPException(status_code=503, detail=ERR_BACKTEST_UNAVAILABLE)
    return jobs


def register_backtests(app: FastAPI, services: Services) -> None:
    """The four routes."""

    @app.post(
        f"{API_PREFIX}/backtests",
        response_model=BacktestRunOut,
        status_code=202,
        operation_id="start_backtest",
        responses={
            409: {"model": ErrorOut, "description": "A run or the planner is active"},
            422: {"model": ErrorOut, "description": "Window not past, or overlay unusable"},
            503: {"model": ErrorOut, "description": "No history or no site configuration"},
        },
    )
    async def start_backtest(body: BacktestRequest) -> BacktestRunOut:
        """Replay local days ``start..end`` (at most 30, all in the past) through the planner
        with the overlay (``planner``/``forecast`` parameters tagged ``whatif``, battery
        settings, a flat tariff). Returns at once; poll ``GET /backtests/{id}``. Nothing is
        written to the configuration or to a device."""
        jobs = _jobs(services)
        try:
            view = await jobs.start(body)
        except BacktestWindowError as exc:
            raise HTTPException(status_code=422, detail=ERR_BACKTEST_WINDOW) from exc
        except BacktestOverlayError as exc:
            detail = OVERLAY_ERRORS.get(exc.key, OVERLAY_ERRORS[OVERLAY_INVALID])
            raise HTTPException(status_code=422, detail=detail) from exc
        except BacktestBusyError as exc:
            raise HTTPException(status_code=409, detail=ERR_BACKTEST_BUSY) from exc
        except BacktestUnavailableError as exc:
            raise HTTPException(status_code=503, detail=ERR_BACKTEST_UNAVAILABLE) from exc
        return _out(view, with_result=False)

    @app.get(
        f"{API_PREFIX}/backtests",
        response_model=list[BacktestRunOut],
        operation_id="list_backtests",
        responses={503: {"model": ErrorOut, "description": "No history"}},
    )
    async def list_backtests() -> list[BacktestRunOut]:
        """The kept runs (the last five), newest first, with progress but without results."""
        return [_out(v, with_result=False) for v in await _jobs(services).runs()]

    @app.get(
        f"{API_PREFIX}/backtests/{{run_id}}",
        response_model=BacktestRunOut,
        operation_id="get_backtest",
        responses={
            404: {"model": ErrorOut, "description": "No such run"},
            503: {"model": ErrorOut, "description": "No history"},
        },
    )
    async def get_backtest(run_id: RunId) -> BacktestRunOut:
        """One run: status, progress while running, the result when done, the reason when it
        failed or was cancelled."""
        view = await _jobs(services).get(run_id)
        if view is None:
            raise HTTPException(status_code=404, detail=ERR_BACKTEST_NOT_FOUND)
        return _out(view, with_result=True)

    @app.delete(
        f"{API_PREFIX}/backtests/{{run_id}}",
        response_model=BacktestCancelOut,
        operation_id="cancel_backtest",
        responses={
            404: {"model": ErrorOut, "description": "No such run"},
            503: {"model": ErrorOut, "description": "No history"},
        },
    )
    async def cancel_backtest(run_id: RunId) -> BacktestCancelOut:
        """Cancel a running run (kept as ``cancelled``) or delete a finished one."""
        outcome = await _jobs(services).cancel(run_id)
        if outcome is None:
            raise HTTPException(status_code=404, detail=ERR_BACKTEST_NOT_FOUND)
        return BacktestCancelOut(outcome=outcome)


__all__ = ["BacktestCancelOut", "BacktestRunOut", "register_backtests"]
