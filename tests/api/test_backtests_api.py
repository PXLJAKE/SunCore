# SPDX-License-Identifier: Apache-2.0
"""``/api/v1/backtests`` (Phase 5 WP6)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Literal

import pytest

from suncore.api import create_app
from suncore.core.decisions import Reason
from suncore.sim.backtest_models import (
    BacktestBusyError,
    BacktestJobView,
    BacktestRequest,
    BacktestUnavailableError,
    BacktestWindowError,
)
from tests.api.conftest import FakeServices, client_for

BODY = {"start": "2026-11-10", "end": "2026-11-12", "params": {"planner.mip_gap": 0.02}}


def view(
    run_id: int = 1, status: Literal["running", "done", "failed", "cancelled"] = "running"
) -> BacktestJobView:
    return BacktestJobView(
        id=run_id,
        status=status,
        created_at=datetime(2026, 11, 23, tzinfo=UTC),
        finished_at=None,
        start=date(2026, 11, 10),
        end=date(2026, 11, 12),
        request=BacktestRequest.model_validate(BODY),
        progress_done=1 if status == "running" else None,
        progress_total=3 if status == "running" else None,
        error=Reason(key="backtest.stop.cancelled") if status == "cancelled" else None,
    )


class FakeJobs:
    def __init__(self) -> None:
        self.started: list[BacktestRequest] = []
        self.fail: Exception | None = None
        self.views = {1: view(), 2: view(2, "cancelled")}

    async def start(self, request: BacktestRequest) -> BacktestJobView:
        if self.fail is not None:
            raise self.fail
        self.started.append(request)
        return self.views[1]

    async def runs(self) -> list[BacktestJobView]:
        return list(self.views.values())

    async def get(self, run_id: int) -> BacktestJobView | None:
        return self.views.get(run_id)

    async def cancel(self, run_id: int) -> Literal["cancelled", "deleted"] | None:
        found = self.views.get(run_id)
        if found is None:
            return None
        return "cancelled" if found.status == "running" else "deleted"


@pytest.fixture
def jobs(services: FakeServices) -> FakeJobs:
    fake = FakeJobs()
    services.backtest_service = fake
    return fake


async def test_start_list_get_cancel(services: FakeServices, jobs: FakeJobs) -> None:
    async with client_for(create_app(services)) as client:
        started = await client.post("/api/v1/backtests", json=BODY)
        assert started.status_code == 202
        body = started.json()
        assert (body["id"], body["status"], body["progress"]) == (
            1,
            "running",
            {"done": 1, "total": 3},
        )
        assert body["request"]["params"] == {"planner.mip_gap": 0.02}
        assert jobs.started == [BacktestRequest.model_validate(BODY)]
        listed = (await client.get("/api/v1/backtests")).json()
        assert [r["id"] for r in listed] == [1, 2]
        assert listed[1]["error"] == {"key": "backtest.stop.cancelled", "args": {}}
        assert (await client.get("/api/v1/backtests/1")).json()["status"] == "running"
        assert (await client.delete("/api/v1/backtests/1")).json() == {"outcome": "cancelled"}
        assert (await client.delete("/api/v1/backtests/2")).json() == {"outcome": "deleted"}
        missing = await client.get("/api/v1/backtests/9")
        assert (missing.status_code, missing.json()["detail"]) == (
            404,
            "api.error.backtest_not_found",
        )
        assert (await client.delete("/api/v1/backtests/9")).status_code == 404


@pytest.mark.parametrize(
    ("error", "status", "detail"),
    [
        (BacktestBusyError(), 409, "api.error.backtest_busy"),
        (BacktestWindowError("x"), 422, "api.error.backtest_window_not_past"),
        (BacktestUnavailableError(), 503, "api.error.backtest_unavailable"),
    ],
)
async def test_start_errors(
    services: FakeServices, jobs: FakeJobs, error: Exception, status: int, detail: str
) -> None:
    jobs.fail = error
    async with client_for(create_app(services)) as client:
        response = await client.post("/api/v1/backtests", json=BODY)
    assert (response.status_code, response.json()["detail"]) == (status, detail)


@pytest.mark.parametrize(
    ("params", "key"),
    [
        ({"controller.reserve_w": 100}, "api.validation.param_not_whatif"),
        ({"controller.tick_s": 5}, "api.validation.param_not_whatif"),
        ({"nope": 1}, "api.validation.param_unknown"),
        ({"planner.budget_s": 999}, "api.validation.param_out_of_range"),
    ],
)
async def test_overlay_validation_rejects_safety_and_control_params(
    services: FakeServices, jobs: FakeJobs, params: dict[str, object], key: str
) -> None:
    async with client_for(create_app(services)) as client:
        response = await client.post("/api/v1/backtests", json={**BODY, "params": params})
    assert response.status_code == 422
    assert response.json()["issues"][0]["key"] == key
    assert jobs.started == []


async def test_window_validation(services: FakeServices, jobs: FakeJobs) -> None:
    async with client_for(create_app(services)) as client:
        reversed_ = await client.post(
            "/api/v1/backtests", json={"start": "2026-11-12", "end": "2026-11-10"}
        )
        too_long = await client.post(
            "/api/v1/backtests", json={"start": "2026-09-01", "end": "2026-11-10"}
        )
    assert reversed_.json()["issues"][0]["key"] == "api.validation.backtest_window_order"
    assert too_long.json()["issues"][0] == {
        "loc": ["body"],
        "key": "api.validation.backtest_window_too_long",
        "args": {"max": 30},
    }


async def test_without_history_every_route_is_503(services: FakeServices) -> None:
    async with client_for(create_app(services)) as client:
        for method, url in (
            ("GET", "/api/v1/backtests"),
            ("GET", "/api/v1/backtests/1"),
            ("DELETE", "/api/v1/backtests/1"),
        ):
            response = await client.request(method, url)
            assert (response.status_code, response.json()["detail"]) == (
                503,
                "api.error.backtest_unavailable",
            )
        started = await client.post("/api/v1/backtests", json=BODY)
        assert started.status_code == 503
