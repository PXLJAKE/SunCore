# SPDX-License-Identifier: Apache-2.0
"""Expert parameters reach the controller, the planner and the tariff (Phase 5 WP5)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from suncore.app.planning import PlanningSettings
from suncore.app.runtime import Runtime
from suncore.app.settings import Settings
from suncore.core.control import SAFETY_LIMITS, ControlSettings
from suncore.core.expert import ExpertConfig
from suncore.core.params import REGISTRY
from tests.app.test_tariff_runtime import fetched, start_runtime


@pytest.fixture
async def runtime(tmp_path: Path) -> AsyncIterator[Runtime]:
    started = await start_runtime(tmp_path, "negative_prices")
    try:
        yield started
    finally:
        await started.stop()


def test_planner_defaults_in_the_registry_are_the_runtime_defaults() -> None:
    """The planner table is a copy of the runtime's defaults; this keeps them in step."""
    planning = PlanningSettings()
    for spec in REGISTRY.values():
        if spec.group != "planner":
            continue
        assert spec.target is not None
        if spec.target == "budget_s":
            assert spec.default == Settings.model_fields["planner_budget_s"].default
        elif spec.target == "mip_gap":
            assert spec.default == Settings.model_fields["planner_mip_gap"].default
        else:
            assert getattr(planning, spec.target) == spec.default, spec.key


def test_the_controller_tick_range_contains_the_app_option_range() -> None:
    option = Settings.model_fields["controller_interval_s"]
    lo = next(m.ge for m in option.metadata if hasattr(m, "ge"))
    hi = next(m.le for m in option.metadata if hasattr(m, "le"))
    spec = REGISTRY["controller.tick_s"]
    assert (spec.minimum, spec.maximum) == (lo, hi)


async def test_without_overrides_the_settings_are_todays(runtime: Runtime) -> None:
    """Golden: no ``expert`` section = the settings the runtime had before Phase 5."""
    assert runtime.expert is None or not runtime.expert.overrides  # the demo stores an empty one
    assert runtime.stack is not None
    assert runtime.planning is not None
    expected = runtime.control_settings or ControlSettings(
        tick_s=float(runtime.settings.controller_interval_s)
    )
    assert runtime.stack.settings == expected
    assert runtime.planning.settings == PlanningSettings(
        budget_s=runtime.settings.planner_budget_s, mip_gap=runtime.settings.planner_mip_gap
    )


async def test_controller_overrides_rebuild_the_stack_with_the_new_settings(
    runtime: Runtime,
) -> None:
    before = runtime.stack
    assert before is not None
    stored = await runtime.put_section(
        "expert",
        ExpertConfig(
            overrides={"controller.min_write_interval_s": 9.0, "controller.smoothing_s": 30}
        ),
    )
    assert stored == runtime.expert
    assert runtime.stack is not None
    assert runtime.stack is not before  # the hand-over path of a device edit
    settings = runtime.stack.settings
    assert settings.min_write_interval_s == 9.0
    assert settings.smoothing_s == 30.0
    assert settings.reserve_w == before.settings.reserve_w  # untouched fields keep their value
    for name, (lo, hi) in SAFETY_LIMITS.items():
        assert lo <= getattr(settings, name) <= hi
    # An override that does not touch the controller leaves the stack alone.
    current = runtime.stack
    await runtime.put_section(
        "expert",
        ExpertConfig(
            overrides={
                "controller.min_write_interval_s": 9.0,
                "controller.smoothing_s": 30,
                "planner.mip_gap": 0.05,
            }
        ),
    )
    assert runtime.stack is current


async def test_planner_overrides_apply_in_place_and_reset_restores_the_defaults(
    runtime: Runtime,
) -> None:
    planning = runtime.planning
    assert planning is not None
    default_budget = planning.settings.budget_s
    await runtime.put_section(
        "expert", ExpertConfig(overrides={"planner.budget_s": 30.0, "planner.mip_gap": 0.02})
    )
    assert runtime.planning is planning  # not rebuilt
    assert planning.settings.budget_s == 30.0
    assert planning.settings.mip_gap == 0.02
    await runtime.put_section("expert", ExpertConfig())
    assert planning.settings.budget_s == default_budget


async def test_the_remunerate_negative_switch_keeps_the_export_price_at_negative_prices(
    runtime: Runtime,
) -> None:
    service = await fetched(runtime)
    negative = [
        s for s in service.report(24).slots if s.import_price is not None and s.import_price < 0
    ]
    assert negative
    assert all(s.export_price == 0.0 for s in negative)  # the region rule, as today
    tariff = runtime.tariff_config
    assert tariff is not None
    switched = tariff.model_copy(
        update={"export": tariff.export.model_copy(update={"remunerate_negative": True})}
    )
    await runtime.put_section("tariff", switched)
    service = await fetched(runtime)
    report = service.report(24)
    assert report.source.negative_price_rule == "none"
    assert report.source.export_zeroed_slots == 0
    assert all(s.export_price == 0.08 for s in report.slots if s.import_price is not None)
