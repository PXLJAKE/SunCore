# SPDX-License-Identifier: Apache-2.0
"""Which situations become notifications (plan WP5, category table).

The engine is pure: it gets an :class:`Observation` (a snapshot the application builds from the
control state, the planner status, the MQTT status, the grid state and the prices) and returns
the :class:`NotifyEvent` s that appeared since the last observation, plus the dedupe keys of
persistent notifications whose cause is gone. The first observation of a device, of the
emergency stop and of the grid flags only sets the baseline, so a restart does not announce what
was already true. Texts are catalogue keys; no free text here.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Final

from suncore.core.decisions import Reason
from suncore.notify.model import Category, NotifyEvent, ProposalRef

FAILED_WRITES: Final = 3
"""Failed writes in a row on one device before an error is announced (plan WP5)."""
PLANNER_FAILED_S: Final = 3600.0
MQTT_LOST_S: Final = 900.0
SUMMARY_HOUR: Final = 20
WINDOW_MIN_SLOTS: Final = 4
"""A cheap window lasts at least 1 h (four 15-minute slots)."""
WINDOW_HORIZON: Final = timedelta(hours=24)
SLOT: Final = timedelta(minutes=15)


@dataclass(frozen=True, slots=True)
class ProposalObs:
    intent_id: str
    device_name: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class DeviceObs:
    device_id: str
    name: str
    last_kind: str | None
    """``DecisionKind`` value of the device's newest decision."""
    last_ts: datetime | None
    tripped: bool
    """The control is paused by a watchdog trip (reason ``safety.watchdog_*``)."""


@dataclass(frozen=True, slots=True)
class Observation:
    now: datetime
    """Timezone-aware, in the site's local zone (the summary hour and window labels use it)."""
    emergency_stop: bool
    global_mode: str
    devices: Sequence[DeviceObs] = ()
    proposals: Sequence[ProposalObs] = ()
    planner_failing_s: float | None = None
    """How long the planner has produced no plan (``None`` = it is fine or not running)."""
    mqtt_enabled: bool = False
    mqtt_connected: bool = False
    island: bool | None = None
    """``None`` = no source (the grid section of PR 6-F supplies it)."""
    para14a: bool | None = None
    prices: Sequence[tuple[datetime, float | None]] = ()
    """Import price per 15-minute slot start (``None`` = unknown)."""


@dataclass(frozen=True, slots=True)
class Evaluation:
    events: tuple[NotifyEvent, ...] = ()
    resolved: tuple[str, ...] = ()
    """Dedupe keys of persistent notifications that can be dismissed."""


def _event(
    category: Category,
    key: str,
    text_key: str,
    now: datetime,
    *,
    persist: bool | None = None,
    **args: str | int | float,
) -> NotifyEvent:
    return NotifyEvent(category, key, Reason(key=text_key, args=args), now, persist=persist)


def cheap_window(
    prices: Sequence[tuple[datetime, float | None]], now: datetime
) -> tuple[datetime, datetime, float] | None:
    """The cheapest contiguous block of at least 1 h below the 25th percentile of the next 24 h.

    Returns ``(start, end, mean_price)`` or ``None``. Slots without a price break a block.
    """
    horizon = now + WINDOW_HORIZON
    slots = sorted(
        (start, price)
        for start, price in prices
        if price is not None and now - SLOT < start < horizon
    )
    if len(slots) < WINDOW_MIN_SLOTS:
        return None
    ordered = sorted(price for _, price in slots)
    threshold = ordered[int(0.25 * (len(ordered) - 1))]
    blocks: list[list[tuple[datetime, float]]] = []
    run: list[tuple[datetime, float]] = []
    for start, price in slots:
        if price >= threshold:  # strictly below the percentile
            run = []
            continue
        if not run or start - run[-1][0] != SLOT:
            run = []
            blocks.append(run)
        run.append((start, price))
    long_blocks = [b for b in blocks if len(b) >= WINDOW_MIN_SLOTS]
    if not long_blocks:
        return None
    best = min(long_blocks, key=lambda b: sum(p for _, p in b) / len(b))
    return best[0][0], best[-1][0] + SLOT, sum(p for _, p in best) / len(best)


@dataclass(slots=True)
class _Acc:
    events: list[NotifyEvent] = field(default_factory=list)
    resolved: list[str] = field(default_factory=list)


class RuleEngine:
    """Stateful transition detector; one instance per running service."""

    def __init__(self) -> None:
        self._seen_proposals: set[str] = set()
        self._failures: dict[str, int] = {}
        self._last_ts: dict[str, datetime | None] = {}
        self._tripped: set[str] | None = None
        self._emergency: bool | None = None
        self._flags: dict[str, bool | None] = {"island": None, "para14a": None}
        self._flag_counts: dict[str, int] = {"island": 0, "para14a": 0}
        self._mqtt_lost_since: datetime | None = None
        self._active: set[str] = set()
        """Persistent error keys currently raised (planner, MQTT): only these get resolved."""
        self._announced_windows: set[str] = set()
        self._summary_day: str | None = None
        self._switched: dict[str, int] = {}
        self._stops = 0

    def evaluate(self, obs: Observation) -> Evaluation:
        acc = _Acc()
        self._proposals(obs, acc)
        self._devices(obs, acc)
        self._safety(obs, acc)
        self._errors(obs, acc)
        self._cheap(obs, acc)
        self._summary(obs, acc)
        return Evaluation(tuple(acc.events), tuple(acc.resolved))

    # --- proposals --------------------------------------------------------------------

    def _proposals(self, obs: Observation, acc: _Acc) -> None:
        self._seen_proposals &= {p.intent_id for p in obs.proposals}
        for proposal in obs.proposals:
            if proposal.intent_id in self._seen_proposals:
                continue
            self._seen_proposals.add(proposal.intent_id)
            minutes = max(1, int((proposal.expires_at - obs.now).total_seconds() // 60))
            acc.events.append(
                NotifyEvent(
                    Category.PROPOSAL,
                    f"proposal:{proposal.intent_id}",
                    Reason(
                        key="notify.proposal.offer",
                        args={"device": proposal.device_name, "minutes": minutes},
                    ),
                    obs.now,
                    proposal=ProposalRef(proposal.intent_id, proposal.expires_at),
                )
            )

    # --- devices: failed writes, trips ------------------------------------------------------

    def _devices(self, obs: Observation, acc: _Acc) -> None:
        tripped_now: set[str] = set()
        for device in obs.devices:
            did = device.device_id
            known = did in self._last_ts
            previous = self._last_ts.get(did)
            self._last_ts[did] = device.last_ts
            if known and device.last_ts is not None and device.last_ts != previous:
                self._decision(device, obs, acc)
            if device.tripped:
                tripped_now.add(did)
                if self._tripped is not None and did not in self._tripped:
                    acc.events.append(
                        _event(
                            Category.ERROR,
                            f"tripped:{did}",
                            "notify.error.device_tripped",
                            obs.now,
                            device=device.name,
                        )
                    )
        if self._tripped is not None:
            acc.resolved.extend(f"tripped:{d}" for d in self._tripped - tripped_now)
        self._tripped = tripped_now

    def _decision(self, device: DeviceObs, obs: Observation, acc: _Acc) -> None:
        did = device.device_id
        count = self._failures.get(did, 0)
        if device.last_kind == "failed":
            count += 1
            if count >= FAILED_WRITES:
                acc.events.append(
                    _event(
                        Category.ERROR,
                        f"write_failed:{did}",
                        "notify.error.write_failed",
                        obs.now,
                        device=device.name,
                        count=count,
                    )
                )
        else:
            if count >= FAILED_WRITES:
                acc.resolved.append(f"write_failed:{did}")
            count = 0
            if device.last_kind == "accepted":
                day = obs.now.date().isoformat()
                self._switched = {day: self._switched.get(day, 0) + 1}
        self._failures[did] = count

    # --- safety: emergency stop, island, section 14a ----------------------------------------

    def _safety(self, obs: Observation, acc: _Acc) -> None:
        if self._emergency is not None and obs.emergency_stop != self._emergency:
            if obs.emergency_stop:
                self._stops += 1
                acc.events.append(
                    _event(
                        Category.SAFETY,
                        f"emergency_stop:{self._stops}",
                        "notify.safety.emergency_stop",
                        obs.now,
                    )
                )
            else:
                acc.resolved.append(f"emergency_stop:{self._stops}")
                acc.events.append(
                    _event(
                        Category.SAFETY,
                        f"emergency_stop_cleared:{self._stops}",
                        "notify.safety.emergency_stop_cleared",
                        obs.now,
                        persist=False,
                    )
                )
        self._emergency = obs.emergency_stop
        self._flag(obs, acc, "island", obs.island)
        self._flag(obs, acc, "para14a", obs.para14a)

    def _flag(self, obs: Observation, acc: _Acc, name: str, value: bool | None) -> None:
        if value is None:
            return
        before = self._flags[name]
        self._flags[name] = value
        if before is None or value == before:
            return
        if value:
            self._flag_counts[name] += 1
            acc.events.append(
                _event(
                    Category.SAFETY,
                    f"{name}:{self._flag_counts[name]}",
                    f"notify.safety.{name}_start",
                    obs.now,
                )
            )
        else:
            count = self._flag_counts[name]
            acc.resolved.append(f"{name}:{count}")
            acc.events.append(
                _event(
                    Category.SAFETY,
                    f"{name}_end:{count}",
                    f"notify.safety.{name}_end",
                    obs.now,
                    persist=False,
                )
            )

    # --- errors: planner, MQTT ----------------------------------------------------------------

    def _errors(self, obs: Observation, acc: _Acc) -> None:
        failing = obs.planner_failing_s
        if failing is not None and failing >= PLANNER_FAILED_S:
            acc.events.append(
                _event(
                    Category.ERROR,
                    "planner_failed",
                    "notify.error.planner_failed",
                    obs.now,
                    hours=int(failing // 3600),
                )
            )
            self._active.add("planner_failed")
        elif failing is None and "planner_failed" in self._active:
            self._active.discard("planner_failed")
            acc.resolved.append("planner_failed")
        if obs.mqtt_enabled and not obs.mqtt_connected:
            if self._mqtt_lost_since is None:
                self._mqtt_lost_since = obs.now
            lost = (obs.now - self._mqtt_lost_since).total_seconds()
            if lost >= MQTT_LOST_S:
                self._active.add("mqtt_lost")
                acc.events.append(
                    _event(
                        Category.ERROR,
                        "mqtt_lost",
                        "notify.error.mqtt_lost",
                        obs.now,
                        minutes=int(lost // 60),
                    )
                )
        else:
            self._mqtt_lost_since = None
            if "mqtt_lost" in self._active:
                self._active.discard("mqtt_lost")
                acc.resolved.append("mqtt_lost")

    # --- cheap windows, daily summary ---------------------------------------------------------

    def _cheap(self, obs: Observation, acc: _Acc) -> None:
        window = cheap_window(obs.prices, obs.now)
        if window is None:
            return
        start, end, mean = window
        key = f"cheap:{start.isoformat()}"
        if key in self._announced_windows:
            return
        if len(self._announced_windows) > 64:
            self._announced_windows.clear()
        self._announced_windows.add(key)
        zone = obs.now.tzinfo
        acc.events.append(
            _event(
                Category.CHEAP_WINDOW,
                key,
                "notify.cheap_window.start",
                obs.now,
                start=start.astimezone(zone).strftime("%H:%M"),
                end=end.astimezone(zone).strftime("%H:%M"),
                price=round(mean, 3),
            )
        )

    def _summary(self, obs: Observation, acc: _Acc) -> None:
        day = obs.now.date().isoformat()
        if obs.now.hour < SUMMARY_HOUR or self._summary_day == day:
            return
        self._summary_day = day
        acc.events.append(
            _event(
                Category.DAILY_SUMMARY,
                f"summary:{day}",
                "notify.daily_summary.text",
                obs.now,
                mode=obs.global_mode,
                switched=self._switched.get(day, 0),
            )
        )
