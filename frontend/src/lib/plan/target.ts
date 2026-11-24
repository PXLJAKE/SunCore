// SPDX-License-Identifier: Apache-2.0
// What the plan chart shows: the live plan, a day, or the plan as it stood at a past time (the
// deep links `#/plan?day=YYYY-MM-DD` from the history view and `#/plan?at=<time>` from the
// decision log), and the time window that belongs to it. Pure: no clock, no DOM.
import { addDays, isDay } from '../history/model';
import type { HoursWindow } from './model';
import { dayInZone, zonedMs } from './zone';

const HOUR_MS = 3_600_000;
const QUARTER_MS = 900_000;
/** The live view starts this long before now so the plan can be held against what happened. */
export const PAST_MS = 3 * HOUR_MS;
/** The plan of a decision is shown from `PAST_MS` before it until this long after it. */
const AFTER_AT_MS = 21 * HOUR_MS;

export type PlanTarget =
  | { kind: 'live'; hours: HoursWindow }
  | { kind: 'day'; day: string }
  | { kind: 'at'; atMs: number };

export interface TimeWindow {
  fromMs: number;
  toMs: number;
}

/** A time of a link: with an offset it is exact, without one it is site time. */
export function parseTime(value: string | null, zone: string): number | null {
  if (!value) return null;
  const naive = /^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}(?::\d{2})?)(?:\.\d+)?$/.exec(value);
  const ms = naive ? zonedMs(naive[1]!, naive[2]!, zone) : Date.parse(value);
  return Number.isNaN(ms) ? null : ms;
}

/** The target of a hash query; `at` wins over `day`, anything invalid falls back to live. */
export function parseTarget(query: URLSearchParams, hours: HoursWindow, zone: string): PlanTarget {
  const atMs = parseTime(query.get('at'), zone);
  if (atMs !== null) return { kind: 'at', atMs };
  const day = query.get('day');
  if (isDay(day)) return { kind: 'day', day };
  return { kind: 'live', hours };
}

export function targetWindow(target: PlanTarget, nowMs: number, zone: string): TimeWindow {
  switch (target.kind) {
    case 'live': {
      const from = Math.floor((nowMs - PAST_MS) / QUARTER_MS) * QUARTER_MS;
      return { fromMs: from, toMs: nowMs + target.hours * HOUR_MS };
    }
    case 'day':
      return {
        fromMs: zonedMs(target.day, '00:00', zone),
        toMs: zonedMs(addDays(target.day, 1), '00:00', zone),
      };
    case 'at': {
      const from = Math.floor((target.atMs - PAST_MS) / QUARTER_MS) * QUARTER_MS;
      return { fromMs: from, toMs: from + PAST_MS + AFTER_AT_MS };
    }
  }
}

/** The site's today on the server's clock. */
export const todayOf = (nowMs: number, zone: string): string => dayInZone(nowMs, zone);

/** Hash that opens the chart for a target (`#/plan` for the live plan). */
export function targetHash(target: PlanTarget, compareMs: number | null = null): string {
  const q = new URLSearchParams();
  if (target.kind === 'day') q.set('day', target.day);
  if (target.kind === 'at') q.set('at', new Date(target.atMs).toISOString());
  if (compareMs !== null) q.set('compare', new Date(compareMs).toISOString());
  const text = q.toString();
  return text ? `#/plan?${text}` : '#/plan';
}
