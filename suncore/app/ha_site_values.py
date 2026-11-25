# SPDX-License-Identifier: Apache-2.0
"""The values of the five site sensors of the HA surface (Phase 7 WP6): pure functions.

Everything here reads the plan, a ``day_summary`` row or ``accuracy_day`` rows and returns a
plain value (``None`` = no source, HA shows "unknown"). Nothing writes, nothing does I/O.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta, tzinfo
from typing import Final

from suncore.core.control import Plan
from suncore.i18n import load_catalogue
from suncore.storage.summaries import AccuracyDayRow, DaySummaryRow

NO_ACTION_KEYS: Final = frozenset({"planner.interval.balanced"})
"""Reason keys that say "nothing to do" (the plan view skips such intervals too)."""
NEXT_ACTION_KEY: Final = "mqtt.next_action.value"
MAX_STATE_CHARS: Final = 255
"""Home Assistant ignores a sensor state longer than this."""


def plan_cost_today(plan: Plan | None, now: datetime, tz: tzinfo) -> float | None:
    """The planned grid cost (import minus export revenue, currency) of the plan's intervals
    that start on the local day of ``now``; ``None`` without a priced interval.

    The plan only holds the time from its creation on, so the figure covers the rest of today.
    """
    if plan is None:
        return None
    today = now.astimezone(tz).date()
    total = 0.0
    priced = False
    for interval in plan.intervals:
        if interval.start.astimezone(tz).date() != today:
            continue
        if interval.expected is None or interval.prices is None:
            continue
        hours = (interval.end - interval.start) / timedelta(hours=1)
        total += (
            (
                interval.expected.import_w * interval.prices.import_price
                - interval.expected.export_w * interval.prices.export_price
            )
            * hours
            / 1000.0
        )
        priced = True
    return total if priced else None


def next_action(plan: Plan | None, now: datetime, tz: tzinfo, language: str) -> str | None:
    """The plan's next planned action as one line in ``language``.

    The first interval that has not ended and whose reasons say more than "balanced"; the text
    is its first reason, rendered by the backend ICU catalogue, with the start day and time in
    front. ``None`` for no plan or nothing to do.
    """
    if plan is None:
        return None
    for interval in plan.intervals:
        if interval.end <= now:
            continue
        reasons = interval.reasons
        if not reasons or all(r.key in NO_ACTION_KEYS for r in reasons):
            continue
        catalogue = load_catalogue(language)
        headline = reasons[0]
        text = catalogue.render(headline.key, dict(headline.args))
        start = interval.start.astimezone(tz)
        days = (start.date() - now.astimezone(tz).date()).days
        day = "today" if days <= 0 else "tomorrow" if days == 1 else "other"
        line = catalogue.render(
            NEXT_ACTION_KEY,
            {
                "day": day,
                "date": start.strftime("%Y-%m-%d"),
                "time": start.strftime("%H:%M"),
                "text": text,
            },
        )
        if len(line) > MAX_STATE_CHARS:
            line = line[: MAX_STATE_CHARS - 1].rstrip() + "…"
        return line
    return None


def savings_today(row: DaySummaryRow | None) -> float | None:
    """What the plant saved today against a house without PV (the first savings tile of the
    history view); ``None`` without priced slots."""
    return None if row is None else row.savings_vs_no_pv


def autarky_today_pct(row: DaySummaryRow | None) -> float | None:
    """Today's autarky in percent; ``None`` without a value."""
    if row is None or row.autarky is None:
        return None
    return row.autarky * 100.0


def pv_forecast_error_w(rows: Sequence[AccuracyDayRow], preferred_source: str) -> float | None:
    """The mean absolute error (W) of the PV forecast on the newest day that has a row.

    The blended forecast (``preferred_source``) is used when it has rows, else every PV source
    counts. The figure is the ``n``-weighted mean over the horizon buckets of that day.
    """
    pv = [r for r in rows if r.kind == "pv" and r.n > 0]
    blended = [r for r in pv if r.source_id == preferred_source]
    pool = blended or pv
    if not pool:
        return None
    newest = max(r.day for r in pool)
    day_rows = [r for r in pool if r.day == newest]
    total = sum(r.n for r in day_rows)
    return sum(r.mae_w * r.n for r in day_rows) / total


__all__ = [
    "autarky_today_pct",
    "next_action",
    "plan_cost_today",
    "pv_forecast_error_w",
    "savings_today",
]
