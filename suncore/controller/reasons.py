# SPDX-License-Identifier: Apache-2.0
"""Catalogue keys of the controller's explanations (``controller.*``).

Every key here exists in ``frontend/src/lib/i18n/locales/{en,de}.json``
(``scripts/check_reasons.py``). Reasons with arguments are built by the helpers below so
the argument names stay in one place.
"""

from __future__ import annotations

from typing import Final

from suncore.core.decisions import Reason

SURPLUS_ON: Final = "controller.surplus_on"
SURPLUS_GONE: Final = "controller.surplus_gone"
SETPOINT_SHIFT: Final = "controller.setpoint_shift"
SETPOINT_BASE: Final = "controller.setpoint_base"
SWITCH_ON: Final = "controller.switch_on"
DEFICIT_BACKOFF: Final = "controller.deficit_backoff"
DEFICIT_BUDGET: Final = "controller.deficit_budget"
BATTERY_RESERVE: Final = "controller.battery_reserve"
BATTERY_TARGET: Final = "controller.battery_target"
GRID_IMPORTING: Final = "controller.grid_importing"
IMPORT_LIMIT: Final = "controller.import_limit"
BASE_OUTSIDE_COMFORT: Final = "controller.base_outside_comfort"
OUTSIDE_WINDOW: Final = "controller.outside_window"
QUIET_HOURS: Final = "controller.quiet_hours"
PLAN_BLOCKED: Final = "controller.plan_blocked"
STATE_UNKNOWN: Final = "controller.state_unknown"
SURPLUS_UNKNOWN: Final = "controller.surplus_unknown"
POWER_UNKNOWN: Final = "controller.power_unknown"
CLIMATE_OFF: Final = "controller.climate_off"
CLIMATE_MODE_UNSUPPORTED: Final = "controller.climate_mode_unsupported"
MIN_RUN_WAIT: Final = "controller.min_run_wait"
MIN_PAUSE_WAIT: Final = "controller.min_pause_wait"
SWITCH_LIMIT: Final = "controller.switch_limit"
WAITING_DELAY: Final = "controller.waiting_delay"
RETRY_WAIT: Final = "controller.retry_wait"
IN_FLIGHT: Final = "controller.in_flight"
NO_SURPLUS: Final = "controller.no_surplus"
IDLE: Final = "controller.idle"
HEAT_LIMIT: Final = "controller.heat_limit"
HEAT_TEMPERATURE_UNKNOWN: Final = "controller.heat_temperature_unknown"
# Phase 4 WP10: the battery band of the plan as battery intents (``controller.battery``).
BATTERY_GRID_CHARGE: Final = "controller.battery.grid_charge"
BATTERY_DISCHARGE_LOCK: Final = "controller.battery.discharge_lock"
BATTERY_RELEASE: Final = "controller.battery.release"
BATTERY_GRID_CHARGE_UNSUPPORTED: Final = "controller.battery.grid_charge_unsupported"
BATTERY_DISCHARGE_LOCK_UNSUPPORTED: Final = "controller.battery.discharge_lock_unsupported"
BATTERY_NO_PLAN: Final = "controller.battery.no_plan"
BATTERY_BLOCKED: Final = "controller.battery.blocked"
# Phase 6 PR 6-F: import side, §14a, island (``controller.grid``).
GRID_ISLAND_SHED: Final = "controller.grid.island_shed"
GRID_ISLAND_KEEP: Final = "controller.grid.island_keep"
GRID_COOLDOWN: Final = "controller.grid.cooldown"
GRID_PARA14A_DIM: Final = "controller.grid.para14a_dim"
GRID_PARA14A_UNKNOWN: Final = "controller.grid.para14a_unknown"
GRID_PARA14A_COOLDOWN: Final = "controller.grid.para14a_cooldown"
GRID_PARA14A_NO_SIGNAL: Final = "controller.grid.para14a_no_signal"
GRID_BACKUP_UNKNOWN: Final = "controller.grid.backup_unknown"
GRID_ISLAND: Final = "controller.grid.island"
GRID_PARA14A_ACTIVE: Final = "controller.grid.para14a_active"
GRID_SHED: Final = "controller.grid.shed"
GRID_FUSE_SHED: Final = "controller.grid.fuse_shed"
GRID_PEAK_LIMIT: Final = "controller.grid.peak_limit"
GRID_BATTERY_SUPPORT: Final = "controller.grid.battery_support"
GRID_BATTERY_ISLAND: Final = "controller.grid.battery_island"
GRID_BATTERY_NO_GRID_CHARGE: Final = "controller.grid.battery_no_grid_charge"
# Phase 6 PR 6-G: the export chain (``controller.export``).
EXPORT_LIMIT: Final = "controller.export.limit"
EXPORT_AC_LIMIT: Final = "controller.export.ac_limit"
EXPORT_NOT_NEEDED: Final = "controller.export.not_needed"
EXPORT_ABSORBING: Final = "controller.export.absorbing"
EXPORT_WAITING: Final = "controller.export.waiting"
EXPORT_INPUTS_UNKNOWN: Final = "controller.export.inputs_unknown"
EXPORT_ISLAND: Final = "controller.export.island"
EXPORT_BLOCKED: Final = "controller.export.blocked"
EXPORT_RELEASE: Final = "controller.export.release"
EXPORT_BATTERY_CAP_LIFTED: Final = "controller.export.battery_cap_lifted"

ALL_KEYS: Final[tuple[str, ...]] = (
    EXPORT_LIMIT,
    EXPORT_AC_LIMIT,
    EXPORT_NOT_NEEDED,
    EXPORT_ABSORBING,
    EXPORT_WAITING,
    EXPORT_INPUTS_UNKNOWN,
    EXPORT_ISLAND,
    EXPORT_BLOCKED,
    EXPORT_RELEASE,
    EXPORT_BATTERY_CAP_LIFTED,
    GRID_ISLAND_SHED,
    GRID_ISLAND_KEEP,
    GRID_COOLDOWN,
    GRID_PARA14A_DIM,
    GRID_PARA14A_UNKNOWN,
    GRID_PARA14A_COOLDOWN,
    GRID_PARA14A_NO_SIGNAL,
    GRID_BACKUP_UNKNOWN,
    GRID_ISLAND,
    GRID_PARA14A_ACTIVE,
    GRID_SHED,
    GRID_FUSE_SHED,
    GRID_PEAK_LIMIT,
    GRID_BATTERY_SUPPORT,
    GRID_BATTERY_ISLAND,
    GRID_BATTERY_NO_GRID_CHARGE,
    BATTERY_GRID_CHARGE,
    BATTERY_DISCHARGE_LOCK,
    BATTERY_RELEASE,
    BATTERY_GRID_CHARGE_UNSUPPORTED,
    BATTERY_DISCHARGE_LOCK_UNSUPPORTED,
    BATTERY_NO_PLAN,
    BATTERY_BLOCKED,
    HEAT_LIMIT,
    HEAT_TEMPERATURE_UNKNOWN,
    SURPLUS_ON,
    SURPLUS_GONE,
    SETPOINT_SHIFT,
    SETPOINT_BASE,
    SWITCH_ON,
    DEFICIT_BACKOFF,
    DEFICIT_BUDGET,
    BATTERY_RESERVE,
    BATTERY_TARGET,
    GRID_IMPORTING,
    IMPORT_LIMIT,
    BASE_OUTSIDE_COMFORT,
    OUTSIDE_WINDOW,
    QUIET_HOURS,
    PLAN_BLOCKED,
    STATE_UNKNOWN,
    SURPLUS_UNKNOWN,
    POWER_UNKNOWN,
    CLIMATE_OFF,
    CLIMATE_MODE_UNSUPPORTED,
    MIN_RUN_WAIT,
    MIN_PAUSE_WAIT,
    SWITCH_LIMIT,
    WAITING_DELAY,
    RETRY_WAIT,
    IN_FLIGHT,
    NO_SURPLUS,
    IDLE,
)


def surplus_on(need_w: float, surplus_w: float) -> Reason:
    return Reason(key=SURPLUS_ON, args={"need_w": round(need_w), "surplus_w": round(surplus_w)})


def surplus_gone(surplus_w: float) -> Reason:
    return Reason(key=SURPLUS_GONE, args={"surplus_w": round(surplus_w)})


def setpoint_shift(shift_c: float, setpoint_c: float) -> Reason:
    return Reason(
        key=SETPOINT_SHIFT, args={"shift_c": round(shift_c, 2), "setpoint_c": round(setpoint_c, 2)}
    )


def setpoint_base(setpoint_c: float) -> Reason:
    return Reason(key=SETPOINT_BASE, args={"setpoint_c": round(setpoint_c, 2)})


def deficit_backoff(deficit_w: float, seconds: float) -> Reason:
    return Reason(
        key=DEFICIT_BACKOFF, args={"deficit_w": round(deficit_w), "seconds": round(seconds)}
    )


def deficit_budget(wh: float) -> Reason:
    return Reason(key=DEFICIT_BUDGET, args={"wh": round(wh, 1)})


def battery_reserve(soc_pct: float, reserve_pct: float) -> Reason:
    return Reason(
        key=BATTERY_RESERVE, args={"soc_pct": round(soc_pct, 1), "reserve_pct": reserve_pct}
    )


def battery_target(soc_pct: float, target_pct: float) -> Reason:
    return Reason(key=BATTERY_TARGET, args={"soc_pct": round(soc_pct, 1), "target_pct": target_pct})


def grid_importing(grid_w: float) -> Reason:
    return Reason(key=GRID_IMPORTING, args={"grid_w": round(grid_w)})


def import_limit(grid_w: float, limit_w: float) -> Reason:
    return Reason(key=IMPORT_LIMIT, args={"grid_w": round(grid_w), "limit_w": round(limit_w)})


def base_outside_comfort(setpoint_c: float, min_c: float, max_c: float) -> Reason:
    return Reason(
        key=BASE_OUTSIDE_COMFORT,
        args={"setpoint_c": round(setpoint_c, 2), "min_c": min_c, "max_c": max_c},
    )


def heat_limit(temperature_c: float, max_c: float) -> Reason:
    return Reason(key=HEAT_LIMIT, args={"temperature_c": round(temperature_c, 1), "max_c": max_c})


def climate_mode_unsupported(mode: str) -> Reason:
    return Reason(key=CLIMATE_MODE_UNSUPPORTED, args={"mode": mode})


def min_run_wait(remaining_s: float) -> Reason:
    return Reason(key=MIN_RUN_WAIT, args={"remaining_s": _ceil(remaining_s)})


def min_pause_wait(remaining_s: float) -> Reason:
    return Reason(key=MIN_PAUSE_WAIT, args={"remaining_s": _ceil(remaining_s)})


def switch_limit(limit: int) -> Reason:
    return Reason(key=SWITCH_LIMIT, args={"limit": limit})


def waiting_delay(remaining_s: float) -> Reason:
    return Reason(key=WAITING_DELAY, args={"remaining_s": _ceil(remaining_s)})


def no_surplus(surplus_w: float, need_w: float) -> Reason:
    return Reason(key=NO_SURPLUS, args={"surplus_w": round(surplus_w), "need_w": round(need_w)})


def grid_shed(grid_w: float, limit_w: float, *, fuse: bool) -> Reason:
    """Shedding above the import limit (``fuse``: above the connection limit, G2)."""
    key = GRID_FUSE_SHED if fuse else GRID_SHED
    return Reason(key=key, args={"grid_w": round(grid_w), "limit_w": round(limit_w)})


def para14a_dim(budget_w: float) -> Reason:
    return Reason(key=GRID_PARA14A_DIM, args={"budget_w": round(budget_w)})


def grid_cooldown(remaining_s: float) -> Reason:
    return Reason(key=GRID_COOLDOWN, args={"remaining_s": _ceil(min(remaining_s, 3600.0))})


def para14a_cooldown(remaining_s: float) -> Reason:
    return Reason(key=GRID_PARA14A_COOLDOWN, args={"remaining_s": _ceil(remaining_s)})


def plain(key: str) -> Reason:
    return Reason(key=key)


def _ceil(seconds: float) -> int:
    return int(seconds) + (1 if seconds > int(seconds) else 0)


__all__ = ["ALL_KEYS"]
