# SPDX-License-Identifier: Apache-2.0
"""Import side, §14a and island in the controller (plan WP6, ADR-0018; PR 6-F).

Deterministic and pure (``core`` only, time injected). Per tick:

* :func:`resolve_grid_inputs` turns the grid configuration, the month's peak and the states of
  the §14a signal and the backup entity into :class:`GridInputs` - only through the resolvers of
  ``core.grid`` (ADR-0018 decision 1), so planner, controller and gate cannot drift;
* :func:`grid_caps` caps the level of each device, like a plan band and without hysteresis
  delay: island sheds every device that is not on the keep list and freezes the kept ones
  (G4); a running cool-down freezes every device (after island) or the §14a devices (G6); an
  active §14a signal shares the budget among the flagged devices in priority order (a device
  keeps its level while ``power <= remaining`` and rises only while ``power + hysteresis <=
  remaining``, so noise in the other devices' draw does not make it flap); an
  unknown signal freezes the flagged devices (G5);
* :func:`shed_order` is the order of the back-off above the import limit (ADR-0018 decision 2):
  continuous loads first, then an explicit ``shed_rank`` (lower first), then reverse priority.

The caps only ever lower a level; rises still need real surplus (the Phase 3 rule), so a shed
device comes back only with export, never while the house imports above the limit.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Final

from suncore.controller import reasons as rs
from suncore.controller.levels import LevelTable
from suncore.core.decisions import Reason
from suncore.core.devices import ContinuousControl, DeviceConfig
from suncore.core.grid import (
    PARA14A_INACTIVE,
    GridState,
    Para14aBudget,
    Para14aStatus,
    effective_export_limit_w,
    effective_import_limit_w,
    grid_state_from,
    para14a_budget_w,
    peak_limit_w,
    static_export_limit_w,
)
from suncore.core.site import GridConfig

POWER_TOLERANCE_W = 1.0


@dataclass(frozen=True, slots=True)
class GridInputs:
    """The grid situation of one tick (the composition root resolves it)."""

    fuse_limit_w: float | None = None
    """``GridConfig.import_limit_w``: the connection/fuse limit."""
    import_limit_w: float | None = None
    """The effective import limit: min(fuse, peak) (``core.grid.effective_import_limit_w``)."""
    peak_limit_w: float | None = None
    """The peak-shaving limit (``None`` = peak shaving off)."""
    export_limit_w: float | None = None
    """The effective export limit (judged from 6-G; passed through to the gate)."""
    para14a: Para14aBudget = PARA14A_INACTIVE
    grid_state: GridState = GridState.NOT_CONFIGURED
    keep_devices: frozenset[str] = frozenset()
    """Island: devices that are not shed (``BackupConfig.keep_devices``)."""
    warnings: tuple[Reason, ...] = ()
    """Situations the user should know about (unknown backup state, §14a without a signal)."""

    @classmethod
    def from_limit(cls, import_limit_w: float | None) -> GridInputs:
        """Phase 3 compatibility: only a fuse limit, nothing else configured."""
        return cls(fuse_limit_w=import_limit_w, import_limit_w=import_limit_w)


def resolve_grid_inputs(
    config: GridConfig,
    *,
    month_peak_w: float | None,
    para14a_state: str | None,
    backup_state: str | None,
    total_kwp: float | None = None,
    export_price: float | None = None,
    export_adapter_verified: bool = False,
) -> GridInputs:
    """The tick's :class:`GridInputs` from the configuration and the entity states.

    ``para14a_state`` / ``backup_state`` are the raw HA states (``None`` = not in the cache).
    §14a enabled without a mapped signal entity cannot be observed: it counts as inactive with
    the warning ``controller.grid.para14a_no_signal`` (the grid operator's own relay path keeps
    acting on the device, ADR-0018 decision 5). A mapped but unavailable signal is ``unknown``
    (G5: no increase of the flagged devices).
    """
    warnings: list[Reason] = []
    if config.para14a and config.para14a_signal is None:
        budget = PARA14A_INACTIVE
        warnings.append(rs.plain(rs.GRID_PARA14A_NO_SIGNAL))
    else:
        budget = para14a_budget_w(
            config.para14a, config.para14a_signal_kind, para14a_state, config.para14a_limit_w
        )
    state = grid_state_from(config.backup, backup_state)
    if state is GridState.UNKNOWN:
        warnings.append(rs.plain(rs.GRID_BACKUP_UNKNOWN))
    static = static_export_limit_w(config.export_limit_w, config.export_limit_pct_kwp, total_kwp)
    return GridInputs(
        fuse_limit_w=config.import_limit_w,
        import_limit_w=effective_import_limit_w(
            config.import_limit_w, config.peak_shaving, month_peak_w
        ),
        peak_limit_w=peak_limit_w(config.peak_shaving, month_peak_w),
        export_limit_w=effective_export_limit_w(
            static,
            zero_export=config.zero_export,
            export_dynamic=config.export_dynamic,
            export_price=export_price,
            adapter_verified=export_adapter_verified,
        ),
        para14a=budget,
        grid_state=state,
        keep_devices=frozenset(config.backup.keep_devices) if config.backup else frozenset(),
        warnings=tuple(warnings),
    )


def situation_reasons(grid: GridInputs) -> tuple[Reason, ...]:
    """What the grid view shows: island, §14a, the peak limit, then the warnings."""
    out: list[Reason] = []
    if grid.grid_state is GridState.ISLAND:
        out.append(rs.plain(rs.GRID_ISLAND))
    if grid.para14a.status is Para14aStatus.ACTIVE and grid.para14a.budget_w is not None:
        out.append(
            Reason(key=rs.GRID_PARA14A_ACTIVE, args={"budget_w": round(grid.para14a.budget_w)})
        )
    elif grid.para14a.status is Para14aStatus.UNKNOWN:
        out.append(rs.plain(rs.GRID_PARA14A_UNKNOWN))
    if grid.peak_limit_w is not None and grid.import_limit_w is not None:
        out.append(Reason(key=rs.GRID_PEAK_LIMIT, args={"limit_w": round(grid.import_limit_w)}))
    out.extend(grid.warnings)
    return tuple(out)


# --- shedding order ---------------------------------------------------------------------------


def shed_key(device: DeviceConfig) -> tuple[int, int, int, int, str]:
    """Sort key of the shedding order: continuous first, explicit rank, reverse priority."""
    continuous = 0 if isinstance(device.control, ContinuousControl) else 1
    ranked = 0 if device.shed_rank is not None else 1
    return (continuous, ranked, device.shed_rank or 0, -device.priority, device.id)


def shed_order(devices: Iterable[DeviceConfig]) -> list[str]:
    """Device ids in the order they are shed above the import limit (first = shed first)."""
    return [d.id for d in sorted(devices, key=shed_key)]


# --- caps ---------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CapInput:
    """One controllable device as :func:`grid_caps` sees it (allocation priority order)."""

    device_id: str
    table: LevelTable
    level: int
    draw_w: float
    """The committed draw this tick."""
    hysteresis_w: float
    para14a: bool = False


@dataclass(frozen=True, slots=True)
class GridCap:
    level: int
    reason: Reason


@dataclass(frozen=True, slots=True)
class GridHolds:
    """The controller's mirror of the gate's cool-down (G6)."""

    island_s: float = 0.0
    para14a_s: float = 0.0


NO_HOLDS: Final = GridHolds()


@dataclass(frozen=True, slots=True)
class GridCaps:
    caps: dict[str, GridCap] = field(default_factory=dict)

    def get(self, device_id: str) -> GridCap | None:
        return self.caps.get(device_id)


def _tighter(current: GridCap | None, new: GridCap) -> GridCap:
    return new if current is None or new.level < current.level else current


def grid_caps(
    devices: Sequence[CapInput],
    grid: GridInputs,
    holds: GridHolds,
    *,
    para14a_fixed_w: float | None = 0.0,
) -> GridCaps:
    """Per device the highest level the grid situation allows (missing = no cap).

    ``para14a_fixed_w`` is the upper power of the flagged devices the controller cannot move
    this tick (not synced); ``None`` = unknown (the flagged devices are then frozen).
    """
    caps: dict[str, GridCap] = {}

    def put(device_id: str, cap: GridCap) -> None:
        caps[device_id] = _tighter(caps.get(device_id), cap)

    _island_caps(devices, grid, holds, put)
    _para14a_caps([d for d in devices if d.para14a], grid, holds, para14a_fixed_w, put)
    return GridCaps(caps)


def _island_caps(
    devices: Sequence[CapInput],
    grid: GridInputs,
    holds: GridHolds,
    put: Callable[[str, GridCap], None],
) -> None:
    """G4 (island: shed all but the keep list, keep frozen) and the G6 hold after it."""
    if grid.grid_state is GridState.ISLAND:
        for d in devices:
            if d.device_id in grid.keep_devices:
                put(d.device_id, GridCap(min(d.level, d.table.top), rs.plain(rs.GRID_ISLAND_KEEP)))
            else:
                put(d.device_id, GridCap(0, rs.plain(rs.GRID_ISLAND_SHED)))
    elif holds.island_s > 0:
        for d in devices:
            put(d.device_id, GridCap(min(d.level, d.table.top), rs.grid_cooldown(holds.island_s)))


def _para14a_caps(
    flagged: Sequence[CapInput],
    grid: GridInputs,
    holds: GridHolds,
    fixed_w: float | None,
    put: Callable[[str, GridCap], None],
) -> None:
    """G3 (share the budget in priority order), G5 (unknown: frozen), G6 (hold after it)."""
    status = grid.para14a.status
    budget = grid.para14a.budget_w
    if status is Para14aStatus.UNKNOWN or (
        status is Para14aStatus.ACTIVE and (budget is None or fixed_w is None)
    ):
        for d in flagged:
            put(d.device_id, GridCap(min(d.level, d.table.top), rs.plain(rs.GRID_PARA14A_UNKNOWN)))
    elif status is Para14aStatus.ACTIVE:
        assert budget is not None
        assert fixed_w is not None
        remaining = budget - fixed_w
        for d in flagged:
            cap = _budget_level(d, remaining)
            put(d.device_id, GridCap(cap, rs.para14a_dim(budget)))
            used = d.table.level(min(d.level, cap)).power_w
            if cap >= d.level:
                used = max(used, d.draw_w)
            remaining -= used
    elif holds.para14a_s > 0:
        for d in flagged:
            put(
                d.device_id,
                GridCap(min(d.level, d.table.top), rs.para14a_cooldown(holds.para14a_s)),
            )


def _budget_level(d: CapInput, remaining_w: float) -> int:
    """The highest level within the remaining budget - never above it (a legal limit).

    The current level and those below it may use the whole remaining budget; a level above the
    current one needs ``hysteresis_w`` to spare, so a device that fits keeps its level and one
    that was dimmed does not climb back on a few watts of noise in the other devices' draw.
    """
    best = 0
    for index in range(d.table.top + 1):
        margin = d.hysteresis_w if index > d.level else 0.0
        if d.table.level(index).power_w + margin <= remaining_w + POWER_TOLERANCE_W:
            best = index
    return best


__all__ = [
    "NO_HOLDS",
    "CapInput",
    "GridCap",
    "GridCaps",
    "GridHolds",
    "GridInputs",
    "grid_caps",
    "resolve_grid_inputs",
    "shed_key",
    "shed_order",
    "situation_reasons",
]
