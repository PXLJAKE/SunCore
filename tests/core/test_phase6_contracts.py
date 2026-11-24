# SPDX-License-Identifier: Apache-2.0
"""PR 6-B contracts: actions, control variants, device options, plan fields, grid resolver,
thermal model, flex runtime state and the new configuration sections."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st
from pydantic import TypeAdapter, ValidationError

from suncore.core.config import (
    SECRET_MASK,
    InverterConfig,
    MqttConfig,
    NotificationsConfig,
    NotifyTarget,
    QuietHours,
)
from suncore.core.control import DeviceBand, GateInputs, PlanInterval
from suncore.core.decisions import (
    Action,
    ChargeAction,
    Decision,
    DecisionKind,
    Intent,
    InverterLimitAction,
    LoadpointAction,
    OperatingMode,
    Origin,
    decision_from_json,
)
from suncore.core.devices import (
    ChargerControl,
    ComfortBand,
    DeviceConfig,
    DeviceKind,
    EvSpec,
    LoadpointControl,
    OnOffControl,
    PowerModel,
    PowerModelKind,
    ProgramSpec,
    SgContacts,
    SgReadyControl,
    ThermalSpec,
    behaviour_mode_capped,
    control_roles,
)
from suncore.core.flex import (
    JOB_TRANSITIONS,
    EvMode,
    EvState,
    EvTarget,
    JobState,
    JobTransitionError,
    ProgramJob,
    ev_energy_need_wh,
    evcc_mode,
    proposed_deadline,
    transition,
)
from suncore.core.grid import (
    BackupConfig,
    GridState,
    Para14aStatus,
    PeakShaving,
    effective_export_limit_w,
    effective_import_limit_w,
    grid_state_from,
    month_bounds,
    month_key,
    para14a_budget_w,
    static_export_limit_w,
    updated_month_peak_w,
)
from suncore.core.identity import identity_token
from suncore.core.site import EntityRef, GridConfig, SiteConfig
from suncore.core.thermal import (
    GENERIC_AIR_WATER_COP,
    CopCurve,
    CopPoint,
    LearnedThermal,
    ThermalModel,
    model_usable,
)

T0 = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)
BERLIN = ZoneInfo("Europe/Berlin")
ACTION: TypeAdapter[Action] = TypeAdapter(Action)
PM = PowerModel(kind=PowerModelKind.NOMINAL, nominal_w=11_000)
watts = st.floats(0, 50_000, allow_nan=False)
opt_watts = st.none() | watts


def err_type(exc: pytest.ExceptionInfo[ValidationError]) -> str:
    return str(exc.value.errors()[0]["type"])


# --- actions --------------------------------------------------------------------------------


@given(
    current=st.floats(0, 500, allow_nan=False),
    phases=st.sampled_from([None, 1, 3]),
)
def test_charge_action_round_trips_through_the_decision_log(
    current: float, phases: int | None
) -> None:
    action = ChargeAction(current_a=current, phases=phases)  # type: ignore[arg-type]
    intent = Intent(id="i", ts=T0, asset_id="wb", action=action, origin=Origin.CONTROLLER)
    decision = Decision(intent=intent, kind=DecisionKind.REJECTED, ts=T0, mode=OperatingMode.SHADOW)
    assert decision_from_json(decision.model_dump_json()) == decision
    assert ACTION.validate_python(action.model_dump()) == action


@pytest.mark.parametrize(
    "bad",
    [
        {"kind": "charge", "current_a": -1},
        {"kind": "charge", "current_a": math.inf},
        {"kind": "charge", "current_a": 10, "phases": 2},
        {"kind": "loadpoint", "mode": "pv"},
        {"kind": "loadpoint", "mode": "smart", "plan_soc_pct": 80},
        {"kind": "loadpoint", "mode": "smart", "max_current_a": 0},
        {"kind": "inverter_limit"},
        {"kind": "inverter_limit", "max_export_w": 0, "max_ac_pct": 50},
        {"kind": "inverter_limit", "max_ac_pct": 101},
    ],
)
def test_invalid_new_actions_are_refused(bad: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        ACTION.validate_python(bad)


def test_valid_new_actions() -> None:
    plan = LoadpointAction(mode="smart", plan_soc_pct=80, plan_time=T0, limit_soc_pct=90)
    assert ACTION.validate_json(plan.model_dump_json()) == plan
    assert InverterLimitAction(max_export_w=0).max_ac_pct is None


# --- control variants -------------------------------------------------------------------------


def charger(**kw: Any) -> ChargerControl:
    return ChargerControl.model_validate({"kind": "charger", "role": "current", **kw})


def test_charger_defaults_and_powers() -> None:
    c = charger(max_current_a=16)
    assert (c.min_current_a, c.phases, c.phase_switch_pause_s, c.max_phase_switches_per_hour) == (
        6.0,
        3,
        120,
        4,
    )
    assert c.max_power_w() == pytest.approx(16 * 230 * 3)
    assert c.min_power_w(1) == pytest.approx(6 * 230)


@pytest.mark.parametrize(
    ("kw", "key"),
    [
        ({"max_current_a": 6}, "charger_current_range"),
        ({"max_current_a": 16, "enable_role": "current"}, "charger_roles_unique"),
        ({"max_current_a": 16, "phase_option_1": "1"}, "charger_phase_options_no_role"),
        (
            {"max_current_a": 16, "phases_role": "ph", "phase_option_1": "1"},
            "charger_phase_options",
        ),
        (
            {
                "max_current_a": 16,
                "phases_role": "ph",
                "phase_option_1": "x",
                "phase_option_3": "x",
            },
            "charger_phase_options",
        ),
        ({"max_current_a": 16, "phase_switching": True}, "charger_phase_switching"),
        (
            {
                "max_current_a": 16,
                "phases": 1,
                "phase_switching": True,
                "phases_role": "ph",
                "phase_option_1": "1p",
                "phase_option_3": "3p",
            },
            "charger_phase_switching",
        ),
    ],
)
def test_charger_validation(kw: dict[str, Any], key: str) -> None:
    with pytest.raises(ValidationError) as exc:
        charger(**kw)
    assert err_type(exc) == f"suncore.{key}"


def sg(**kw: Any) -> SgReadyControl:
    base: dict[str, Any] = {
        "kind": "sg_ready",
        "contact_a": "a",
        "contact_b": "b",
        "normal": {"a": False, "b": False},
        "intensified": {"a": True, "b": False},
    }
    return SgReadyControl.model_validate({**base, **kw})


def test_sg_ready_steps_never_contain_the_block_state() -> None:
    plain = sg()
    assert plain.steps == (SgContacts(a=False, b=False), SgContacts(a=True, b=False))
    forced_not_allowed = sg(forced={"a": True, "b": True})
    assert len(forced_not_allowed.steps) == 2
    allowed = sg(forced={"a": True, "b": True}, allow_forced=True)
    assert len(allowed.steps) == 3
    assert control_roles(allowed) == ("a", "b")
    assert allowed.min_dwell_s == 600


@pytest.mark.parametrize(
    ("kw", "key"),
    [
        ({"contact_b": "a"}, "sg_contacts_unique"),
        ({"intensified": {"a": False, "b": False}}, "sg_states_distinct"),
        ({"allow_forced": True}, "sg_forced_missing"),
    ],
)
def test_sg_ready_validation(kw: dict[str, Any], key: str) -> None:
    with pytest.raises(ValidationError) as exc:
        sg(**kw)
    assert err_type(exc) == f"suncore.{key}"
    with pytest.raises(ValidationError):
        sg(min_dwell_s=60)  # at least 10 min (plan §0 decision 9)


def test_loadpoint_has_no_entity_role_and_checks_its_topic() -> None:
    lp = LoadpointControl(kind="loadpoint", loadpoint_id=1)
    assert lp.topic_prefix == "evcc"
    assert control_roles(lp) == ()
    for prefix in ("evcc/#", "", "a b", "+/x"):
        with pytest.raises(ValidationError):
            LoadpointControl(kind="loadpoint", loadpoint_id=1, topic_prefix=prefix)
    with pytest.raises(ValidationError):
        LoadpointControl(kind="loadpoint", loadpoint_id=0)


# --- device options --------------------------------------------------------------------------


def wallbox(**kw: Any) -> DeviceConfig:
    base: dict[str, Any] = {
        "id": "wb",
        "template_id": "generic.wallbox",
        "name": "Wallbox",
        "kind": DeviceKind.WALLBOX,
        "entities": {
            "current": EntityRef(entity_id="number.wb_current"),
            "soc": EntityRef(entity_id="sensor.car_soc"),
        },
        "power_model": PM,
        "control": charger(max_current_a=16),
    }
    return DeviceConfig.model_validate({**base, **kw})


def test_device_kind_checks_of_phase6_options() -> None:
    ok = wallbox(ev=EvSpec(capacity_wh=60_000, vehicle_soc_role="soc"), para14a=True, shed_rank=1)
    assert ok.ev is not None
    assert ok.ev.default_mode is EvMode.PV
    cases: list[tuple[dict[str, Any], str]] = [
        ({"kind": DeviceKind.SWITCH}, "control_kind_mismatch"),
        ({"ev": EvSpec(vehicle_soc_role="missing", capacity_wh=1)}, "ev_role_unmapped"),
        (
            {"program": ProgramSpec(runtime_min=60, start_method="notify")},
            "option_not_applicable",
        ),
        ({"thermal": ThermalSpec()}, "option_not_applicable"),
    ]
    for kw, key in cases:
        with pytest.raises(ValidationError) as exc:
            wallbox(**kw)
        assert err_type(exc) == f"suncore.{key}"
    with pytest.raises(ValidationError) as exc:
        DeviceConfig(
            id="hp",
            template_id="generic.hp",
            name="HP",
            kind=DeviceKind.WALLBOX,
            entities={"a": EntityRef(entity_id="switch.a"), "b": EntityRef(entity_id="switch.b")},
            power_model=PM,
            control=sg(),
        )
    assert err_type(exc) == "suncore.control_kind_mismatch"


def test_program_spec_rules() -> None:
    assert ProgramSpec(runtime_min=95, start_method="notify", profile_wh=(1,) * 7).idle_w == 5.0
    with pytest.raises(ValidationError) as exc:
        ProgramSpec(runtime_min=95, start_method="notify", profile_wh=(1,) * 6)
    assert err_type(exc) == "suncore.program_profile_length"
    with pytest.raises(ValidationError) as exc:
        ProgramSpec(runtime_min=60, start_method="plug")
    assert err_type(exc) == "suncore.program_plug_resume"
    plug = ProgramSpec(runtime_min=60, start_method="plug", resume_confirmed=True)
    with pytest.raises(ValidationError) as exc:
        DeviceConfig(
            id="washer",
            template_id="generic.program",
            name="Washer",
            kind=DeviceKind.PROGRAM,
            entities={"m": EntityRef(entity_id="number.m")},
            power_model=PM,
            control=charger(max_current_a=16).model_copy(update={"role": "m"}),
            program=plug,
        )
    # the charger control is caught first (it needs a wallbox)
    assert err_type(exc) == "suncore.control_kind_mismatch"


def test_ev_spec_rules() -> None:
    with pytest.raises(ValidationError) as exc:
        EvSpec(vehicle_soc_role="soc")
    assert err_type(exc) == "suncore.ev_soc_needs_capacity"
    with pytest.raises(ValidationError) as exc:
        EvSpec(default_mode=EvMode.TARGET)
    assert err_type(exc) == "suncore.ev_default_target"


def test_behaviour_confirmation_is_identity_and_old_tokens_are_unchanged() -> None:
    switch = DeviceConfig(
        id="pool",
        template_id="generic.switch",
        name="Pool",
        kind=DeviceKind.SWITCH,
        entities={"switch": EntityRef(entity_id="switch.pool")},
        power_model=PM,
        control=OnOffControl(kind="on_off", role="switch"),
    )
    # The token of a Phase 3 device is what it was before PR 6-B (no device drops to shadow).
    legacy = json.dumps(
        ["pool", "generic.switch", [["switch", "switch.pool"]]], separators=(",", ":")
    )
    assert identity_token(switch) == hashlib.sha256(legacy.encode()).hexdigest()[:16]
    assert not behaviour_mode_capped(switch)
    unconfirmed = wallbox()
    confirmed = wallbox(behaviour_confirmed=True)
    assert behaviour_mode_capped(unconfirmed)
    assert not behaviour_mode_capped(confirmed)
    assert identity_token(unconfirmed) != identity_token(confirmed)


def test_thermal_spec_only_on_thermal_kinds() -> None:
    hp = DeviceConfig(
        id="hp",
        template_id="generic.hp",
        name="HP",
        kind=DeviceKind.HEAT_PUMP,
        entities={"a": EntityRef(entity_id="switch.a"), "b": EntityRef(entity_id="switch.b")},
        power_model=PM,
        control=sg(),
        comfort=ComfortBand(min_c=45, max_c=55),
        thermal=ThermalSpec(grid_preheat=False),
    )
    assert hp.thermal is not None
    assert hp.thermal.cop is None  # generic assumption applies


# --- plan fields -------------------------------------------------------------------------------


@given(allowed=st.booleans(), start=st.booleans(), grid=opt_watts, cap=opt_watts)
def test_device_band_invariants(
    allowed: bool, start: bool, grid: float | None, cap: float | None
) -> None:
    try:
        band = DeviceBand(allowed=allowed, start=start, grid_power_max_w=grid, max_power_w=cap)
    except ValidationError:
        assert (
            (start and not allowed)
            or (grid is not None and cap is not None and grid > cap)
            or (grid is not None and grid > 0 and not allowed)
        )
        return
    assert not band.start or band.allowed
    if band.grid_power_max_w is not None and band.max_power_w is not None:
        assert band.grid_power_max_w <= band.max_power_w


def test_old_plan_intervals_still_load_and_defaults() -> None:
    interval = PlanInterval.model_validate(
        {
            "start": "2026-11-24T12:00:00Z",
            "end": "2026-11-24T12:15:00Z",
            "devices": {"pool": {"allowed": True, "preference": "neutral", "max_power_w": None}},
        }
    )
    band = interval.devices["pool"]
    assert (band.grid_power_max_w, band.start) == (None, False)
    inputs = GateInputs()
    assert inputs.grid_state is GridState.UNKNOWN
    assert inputs.para14a_status is Para14aStatus.INACTIVE
    assert (inputs.export_limit_w, inputs.para14a_budget_w) == (None, None)


# --- grid resolver ------------------------------------------------------------------------------


@given(
    fuse=opt_watts,
    enabled=st.booleans(),
    floor=watts,
    month_peak=opt_watts,
)
def test_effective_import_limit_is_never_above_the_fuse(
    fuse: float | None, enabled: bool, floor: float, month_peak: float | None
) -> None:
    peak = PeakShaving(enabled=enabled, peak_floor_w=floor)
    limit = effective_import_limit_w(fuse, peak, month_peak)
    if fuse is not None:
        assert limit is not None
        assert limit <= fuse
    if not enabled:
        assert limit == fuse
    else:
        assert limit is not None
        expected_peak = max(month_peak or 0.0, floor)
        assert limit == (expected_peak if fuse is None else min(fuse, expected_peak))
        assert limit >= min(floor, fuse if fuse is not None else floor)


@given(peak=opt_watts, quarter=st.floats(-50_000, 50_000, allow_nan=False))
def test_month_peak_only_rises(peak: float | None, quarter: float) -> None:
    new = updated_month_peak_w(peak, quarter)
    assert new >= (peak or 0.0)
    assert new >= 0
    assert new == max(peak or 0.0, quarter, 0.0)


@given(
    static_w=opt_watts,
    pct=st.none() | st.floats(0, 100, allow_nan=False),
    kwp=st.none() | st.floats(0.1, 100, allow_nan=False),
    zero=st.booleans(),
    dynamic=st.booleans(),
    price=st.none() | st.floats(-1, 1, allow_nan=False),
    verified=st.booleans(),
)
def test_effective_export_limit_is_never_above_the_static_one(  # noqa: PLR0917
    static_w: float | None,
    pct: float | None,
    kwp: float | None,
    zero: bool,
    dynamic: bool,
    price: float | None,
    verified: bool,
) -> None:
    static = static_export_limit_w(static_w, pct, kwp)
    limit = effective_export_limit_w(
        static,
        zero_export=zero,
        export_dynamic=dynamic,
        export_price=price,
        adapter_verified=verified,
    )
    if static is not None:
        assert limit is not None
        assert limit <= static
    if zero or (dynamic and verified and price is not None and price < 0):
        assert limit == 0
    else:
        assert limit == static  # an unverified adapter never makes the dynamic limit apply


def test_static_export_limit_from_percent() -> None:
    assert static_export_limit_w(None, 70, 10) == pytest.approx(7000)
    assert static_export_limit_w(13_500, None, None) == 13_500
    assert static_export_limit_w(None, 70, None) is None


@given(
    kind=st.sampled_from(["binary", "power_w"]),
    state=st.none()
    | st.sampled_from(["on", "off", "unavailable", "unknown", "garbage", "nan"])
    | st.floats(-10, 20_000, allow_nan=False).map(str),
    limit=st.floats(100, 20_000, allow_nan=False),
)
def test_para14a_budget_never_exceeds_the_limit(kind: str, state: str | None, limit: float) -> None:
    budget = para14a_budget_w(True, kind, state, limit)  # type: ignore[arg-type]
    if budget.status is Para14aStatus.ACTIVE:
        assert budget.budget_w is not None
        assert 0 < budget.budget_w <= limit
    else:
        assert budget.budget_w is None
    if state is None or state in ("unavailable", "unknown", "garbage", "nan"):
        assert budget.status is Para14aStatus.UNKNOWN  # G5: unknown freezes flagged devices
    assert para14a_budget_w(False, kind, state, limit).status is Para14aStatus.INACTIVE  # type: ignore[arg-type]


def test_para14a_binary_and_power_signals() -> None:
    assert para14a_budget_w(True, "binary", "on", 4200).budget_w == 4200
    assert para14a_budget_w(True, "binary", "off", 4200).status is Para14aStatus.INACTIVE
    assert para14a_budget_w(True, "power_w", "0", 4200).status is Para14aStatus.INACTIVE
    assert para14a_budget_w(True, "power_w", "3000", 4200).budget_w == 3000
    assert para14a_budget_w(True, "power_w", "-1", 4200).status is Para14aStatus.UNKNOWN


def test_grid_state_from_the_backup_entity() -> None:
    backup = BackupConfig(entity_id="sensor.backup_mode", island_states=("island", "backup"))
    assert grid_state_from(backup, "island") is GridState.ISLAND
    assert grid_state_from(backup, "grid") is GridState.CONNECTED
    assert grid_state_from(backup, "unavailable") is GridState.UNKNOWN
    assert grid_state_from(backup, None) is GridState.UNKNOWN
    assert grid_state_from(None, "island") is GridState.UNKNOWN
    with pytest.raises(ValidationError) as exc:
        BackupConfig(entity_id="sensor.b", island_states=("unavailable",))
    assert err_type(exc) == "suncore.backup_unavailable_state"
    with pytest.raises(ValidationError):
        BackupConfig(entity_id="sensor.b", island_states=())


@pytest.mark.parametrize(
    ("at", "key", "hours"),
    [
        (datetime(2026, 3, 15, 12, tzinfo=UTC), "2026-03", 31 * 24 - 1),  # spring forward
        (datetime(2026, 10, 15, 12, tzinfo=UTC), "2026-10", 31 * 24 + 1),  # fall back
        (datetime(2026, 11, 30, 23, 30, tzinfo=UTC), "2026-12", 31 * 24),  # local is December
        (datetime(2026, 12, 31, 23, 30, tzinfo=UTC), "2027-01", 31 * 24),  # year change
    ],
)
def test_month_bounds_are_dst_safe(at: datetime, key: str, hours: int) -> None:
    assert month_key(at, BERLIN) == key
    start, end = month_bounds(at, BERLIN)
    assert start <= at < end
    assert end - start == timedelta(hours=hours)
    assert start.astimezone(BERLIN).hour == 0
    with pytest.raises(ValueError, match="aware"):
        month_key(at.replace(tzinfo=None), BERLIN)


def test_grid_config_additions_have_safe_defaults_and_old_sites_load() -> None:
    old = {"power": {"entity_id": "sensor.grid"}, "import_limit_w": 11_000}
    grid = GridConfig.model_validate(old)
    assert not grid.peak_shaving.enabled
    assert (grid.export_dynamic, grid.zero_export, grid.para14a_limit_w) == (False, False, 4200)
    assert grid.para14a_signal_kind == "binary"
    assert grid.backup is None
    with pytest.raises(ValidationError) as exc:
        GridConfig.model_validate(
            {**old, "peak_shaving": {"enabled": True, "peak_floor_w": 12_000}}
        )
    assert err_type(exc) == "suncore.peak_floor_above_fuse"
    site = SiteConfig(
        grid=GridConfig.model_validate(
            {**old, "backup": {"entity_id": "sensor.backup", "island_states": ["on"]}}
        )
    )
    assert "sensor.backup" in {r.entity_id for r in site.entity_refs()}
    with pytest.raises(ValidationError) as exc:
        SiteConfig(
            grid=GridConfig.model_validate(
                {**old, "backup": {"entity_id": "sensor.grid", "island_states": ["on"]}}
            )
        )
    assert err_type(exc) == "suncore.entity_reused"


# --- thermal --------------------------------------------------------------------------------------


@given(t=st.floats(-40, 50, allow_nan=False))
def test_cop_curve_is_clamped_and_between_neighbours(t: float) -> None:
    cop = GENERIC_AIR_WATER_COP.cop_at(t)
    values = [p.cop for p in GENERIC_AIR_WATER_COP.points]
    assert min(values) <= cop <= max(values)
    assert GENERIC_AIR_WATER_COP.assumed


def test_cop_curve_rules() -> None:
    assert GENERIC_AIR_WATER_COP.cop_at(2) == pytest.approx(3.0)
    assert GENERIC_AIR_WATER_COP.cop_at(-20) == pytest.approx(2.5)
    assert GENERIC_AIR_WATER_COP.cop_at(4.5) == pytest.approx(3.3)
    with pytest.raises(ValidationError) as exc:
        CopCurve(points=(CopPoint(outdoor_c=5, cop=3), CopPoint(outdoor_c=5, cop=4)))
    assert err_type(exc) == "suncore.cop_curve_order"


@given(
    c=st.floats(1, 10_000, allow_nan=False),
    ua=st.floats(0, 1000, allow_nan=False),
    t0=st.floats(-20, 90, allow_nan=False),
    out=st.floats(-20, 40, allow_nan=False),
    heat=st.floats(0, 10_000, allow_nan=False),
    dt=st.floats(0, 86_400, allow_nan=False),
)
def test_rc_step_moves_towards_equilibrium_without_overshoot(  # noqa: PLR0917
    c: float, ua: float, t0: float, out: float, heat: float, dt: float
) -> None:
    model = ThermalModel(capacity_wh_per_k=c, loss_w_per_k=ua)
    t1 = model.step(t0, out, heat, dt)
    assert math.isfinite(t1)
    if ua == 0:
        assert t1 >= t0
        return
    t_eq = out + heat / ua
    assert min(t0, t_eq) - 1e-6 <= t1 <= max(t0, t_eq) + 1e-6
    assume(dt > 0)
    assert abs(t1 - t_eq) <= abs(t0 - t_eq) + 1e-9


def test_model_usable_needs_quality_and_history() -> None:
    model = ThermalModel(capacity_wh_per_k=200, loss_w_per_k=100)
    good = LearnedThermal(model=model, nrmse=0.1, history_days=20, fitted_at=T0)
    assert model_usable(good)
    assert not model_usable(good.model_copy(update={"nrmse": 0.3}))
    assert not model_usable(good.model_copy(update={"history_days": 7}))
    assert not model_usable(None)
    with pytest.raises(ValueError, match="negative"):
        model.step(20, 0, 0, -1)


# --- flex runtime state -------------------------------------------------------------------------


def job(**kw: Any) -> ProgramJob:
    base: dict[str, Any] = {
        "id": "j1",
        "device_id": "washer",
        "deadline": T0 + timedelta(hours=6),
        "armed_at": T0,
    }
    return ProgramJob.model_validate({**base, **kw})


@given(path=st.lists(st.sampled_from(list(JobState)), max_size=6))
def test_job_transitions_follow_the_table_and_running_only_ends_done(
    path: list[JobState],
) -> None:
    current = job()
    for step in path:
        allowed = step in JOB_TRANSITIONS[current.state]
        try:
            nxt = transition(current, step, T0 + timedelta(minutes=5), planned_start=T0)
        except JobTransitionError:
            assert not allowed
            continue
        assert allowed
        if current.state is JobState.RUNNING:
            assert nxt.state is JobState.DONE  # P2: never cancelled while running
        current = nxt
    if current.final:
        assert JOB_TRANSITIONS[current.state] == frozenset()


def test_job_validation() -> None:
    with pytest.raises(ValidationError) as exc:
        job(deadline=T0)
    assert err_type(exc) == "suncore.job_deadline_past"
    with pytest.raises(ValidationError):
        job(state="scheduled")
    with pytest.raises(JobTransitionError):
        transition(job(), JobState.SCHEDULED, T0)  # needs planned_start
    with pytest.raises(JobTransitionError):
        transition(job(), JobState.DONE, T0)


def test_ev_state_and_target() -> None:
    with pytest.raises(ValidationError) as exc:
        EvState(device_id="wb", mode=EvMode.TARGET, updated_at=T0)
    assert err_type(exc) == "suncore.ev_mode_target_missing"
    with pytest.raises(ValidationError) as exc:
        EvTarget(by=T0)
    assert err_type(exc) == "suncore.ev_target_one"
    with pytest.raises(ValidationError):
        EvTarget(by=T0, soc_pct=80, energy_wh=1000)
    assert EvState(device_id="wb", updated_at=T0).mode is EvMode.PV
    assert [evcc_mode(m) for m in EvMode] == ["off", "now", "smart", "smart", "smart"]


@given(
    target=st.floats(0, 100, allow_nan=False),
    soc=st.floats(0, 100, allow_nan=False),
    cap=st.floats(1000, 200_000, allow_nan=False),
    eff=st.floats(0.5, 1, allow_nan=False),
)
def test_ev_energy_need(target: float, soc: float, cap: float, eff: float) -> None:
    need = ev_energy_need_wh(target, soc, cap, eff)
    assert need >= 0
    stored = max(0.0, target - soc) / 100 * cap
    assert need >= stored - 1e-6
    with pytest.raises(ValueError, match="efficiency"):
        ev_energy_need_wh(target, soc, cap, 0)


@pytest.mark.parametrize(
    ("now", "runtime", "expected"),
    [
        (datetime(2026, 11, 24, 9, tzinfo=BERLIN), 2, datetime(2026, 11, 24, 18, tzinfo=BERLIN)),
        (datetime(2026, 11, 24, 17, tzinfo=BERLIN), 2, datetime(2026, 11, 25, 18, tzinfo=BERLIN)),
        # 40 h does not fit before tomorrow's 18:00 either: now + 12 h
        (datetime(2026, 11, 24, 9, tzinfo=BERLIN), 40, datetime(2026, 11, 24, 21, tzinfo=BERLIN)),
        # DST end night: 18:00 local on the next day
        (datetime(2026, 10, 24, 19, tzinfo=BERLIN), 3, datetime(2026, 10, 25, 18, tzinfo=BERLIN)),
    ],
)
def test_proposed_deadline(now: datetime, runtime: int, expected: datetime) -> None:
    assert proposed_deadline(now, timedelta(hours=runtime)) == expected
    with pytest.raises(ValueError, match="aware"):
        proposed_deadline(now.replace(tzinfo=None), timedelta(hours=1))


# --- configuration sections -------------------------------------------------------------------


def test_mqtt_defaults_and_rules() -> None:
    m = MqttConfig()
    assert (m.enabled, m.broker, m.discovery_prefix, m.allow_mode_raise) == (
        True,
        "supervisor",
        "homeassistant",
        False,
    )
    cases: list[tuple[dict[str, Any], str]] = [
        ({"broker": "manual"}, "mqtt_manual_needs_host"),
        ({"host": "core-mosquitto"}, "mqtt_supervisor_extra"),
        ({"broker": "manual", "host": "h", "password": "x"}, "mqtt_password_needs_user"),
    ]
    for kw, key in cases:
        with pytest.raises(ValidationError) as exc:
            MqttConfig.model_validate(kw)
        assert err_type(exc) == f"suncore.{key}"
    for bad in ({"port": 0}, {"discovery_prefix": "home/assistant"}, {"host": "-bad"}):
        with pytest.raises(ValidationError):
            MqttConfig.model_validate({"broker": "manual", "host": "h", **bad})


@given(password=st.text(min_size=1, max_size=50))
def test_mqtt_password_never_leaves_in_clear_text(password: str) -> None:
    m = MqttConfig(broker="manual", host="h", username="u", password=password)
    assert json.loads(m.model_dump_json())["password"] == SECRET_MASK
    assert f"SecretStr({password!r})" not in repr(m)
    revealed = m.model_dump_json(context={"reveal_secrets": True})
    assert MqttConfig.model_validate_json(revealed) == m


def test_notifications_defaults_and_rules() -> None:
    n = NotificationsConfig()
    assert n.persistent
    assert n.categories.error
    assert n.categories.safety
    assert not n.categories.cheap_window
    assert not n.categories.daily_summary
    assert (n.quiet_hours.start, n.quiet_hours.end, n.daily_push_cap) == ("22:00", "07:00", 20)
    assert not n.push_enabled  # push only after the user picked a target
    target = NotifyTarget(kind="service", id="mobile_app_pixel")
    assert NotificationsConfig(targets=(target,)).push_enabled
    for kind, ident in (
        ("entity", "light.kitchen"),
        ("service", "send_message"),
        ("service", "Mobile App"),
    ):
        with pytest.raises(ValidationError):
            NotifyTarget(kind=kind, id=ident)  # type: ignore[arg-type]
    with pytest.raises(ValidationError) as exc:
        NotificationsConfig(targets=(target, target))
    assert err_type(exc) == "suncore.notify_targets_unique"
    with pytest.raises(ValidationError):
        QuietHours(start="22:00", end="22:00")


def test_inverter_defaults_and_rules() -> None:
    assert not InverterConfig().mode_capped
    modbus = InverterConfig(adapter="kostal_modbus", capability="ac_limit", nominal_ac_w=10_000)
    assert modbus.mode_capped  # X6 until the verification test
    assert not modbus.model_copy(update={"limit_verified": True}).mode_capped
    cases: list[tuple[dict[str, Any], str]] = [
        ({"limit_verified": True}, "inverter_none_extra"),
        ({"adapter": "kostal_rest"}, "inverter_needs_capability"),
        ({"adapter": "kostal_rest", "capability": "ac_limit"}, "inverter_capability_mismatch"),
        ({"adapter": "generic_number", "capability": "export_limit"}, "inverter_entity"),
        (
            {
                "adapter": "generic_number",
                "capability": "ac_limit",
                "limit_entity": {"entity_id": "number.limit"},
            },
            "inverter_ac_needs_nominal",
        ),
    ]
    for kw, key in cases:
        with pytest.raises(ValidationError) as exc:
            InverterConfig.model_validate(kw)
        assert err_type(exc) == f"suncore.{key}"
