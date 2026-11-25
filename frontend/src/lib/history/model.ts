// SPDX-License-Identifier: Apache-2.0
// Pure logic of the history view: period navigation on the server's own calendar days, the date
// ranges of the analysis charts, the URL state and the figures the tiles show. The browser clock
// is never used: "today" is whatever the server says (the demo runs on a simulated date), and
// every day is a `YYYY-MM-DD` string so no time zone arithmetic is needed.
import type { Granularity, HistorySummary } from '../api/types';

export const PERIODS: readonly Granularity[] = ['day', 'week', 'month', 'year'];

const DAY_MS = 86_400_000;
const DAY_RE = /^\d{4}-\d{2}-\d{2}$/;

export const isPeriod = (v: string | null | undefined): v is Granularity =>
  PERIODS.includes(v as Granularity);

/** `YYYY-MM-DD` of an ISO timestamp or date (the site's local day: the offset is not applied). */
export const dayOf = (iso: string): string => iso.slice(0, 10);

export function addDays(day: string, n: number): string {
  const [y, m, d] = day.split('-').map(Number) as [number, number, number];
  return new Date(Date.UTC(y, m - 1, d) + n * DAY_MS).toISOString().slice(0, 10);
}

export const isDay = (v: string | null | undefined): v is string =>
  typeof v === 'string' && DAY_RE.test(v) && !Number.isNaN(Date.parse(v));

/** State of the view as the URL carries it: `#/history?period=week&date=2026-06-15`. */
export interface HistoryState {
  period: Granularity;
  /** Any day inside the period; `null` = the period containing today (server clock). */
  date: string | null;
}

export function parseHistoryQuery(query: URLSearchParams): HistoryState {
  const period = query.get('period');
  const date = query.get('date');
  return { period: isPeriod(period) ? period : 'week', date: isDay(date) ? date : null };
}

export function historyHash(state: HistoryState): string {
  const q = new URLSearchParams({ period: state.period });
  if (state.date) q.set('date', state.date);
  return `#/history?${q.toString()}`;
}

/** The hash that opens the plan view on a day (read by the plan chart, PR 5-F). */
export const planDayHash = (day: string): string => `#/plan?day=${day}`;

/** Day that shows the period before (`-1`) or after (`1`) the summarised one. */
export function neighbour(summary: HistorySummary, dir: -1 | 1): string {
  return dir < 0 ? addDays(dayOf(summary.start), -1) : dayOf(summary.end);
}

export interface Range {
  /** First day, inclusive. */
  from: string;
  /** Last day, inclusive (never after today: `days` counts up to and including it). */
  to: string;
}

/** Days the heatmap averages over at least (a single week says little about habits). */
export const HEATMAP_DAYS = 28;
/** Days the trend charts show for a single day. */
export const TREND_DAYS = 14;

const spanDays = (r: Range): number =>
  Math.round((Date.parse(r.to) - Date.parse(r.from)) / DAY_MS) + 1;

/** The days of the period that have happened (up to and including today). */
export function periodRange(summary: HistorySummary): Range {
  const from = dayOf(summary.start);
  return { from, to: addDays(from, Math.max(summary.days, 1) - 1) };
}

/** Cycles and forecast skill: the period, or the 14 days up to a day. */
export function trendRange(summary: HistorySummary): Range {
  const r = periodRange(summary);
  return summary.period === 'day' ? { from: addDays(r.to, -(TREND_DAYS - 1)), to: r.to } : r;
}

/** The heatmap needs several weeks to average over: at least 28 days. */
export function heatmapRange(summary: HistorySummary): Range {
  const r = periodRange(summary);
  return spanDays(r) >= HEATMAP_DAYS ? r : { from: addDays(r.to, -(HEATMAP_DAYS - 1)), to: r.to };
}

/** Site time without offset, as the endpoints that take datetimes read it. */
export const startOfDay = (day: string): string => `${day}T00:00:00`;
export const endOfRange = (r: Range): string => startOfDay(addDays(r.to, 1));

/** Whether anything was measured; `false` = the collecting state. */
export const hasData = (s: HistorySummary | null): boolean => !!s && s.days_with_data > 0;

/** Days of the period that happened but are missing or too sparse to count (marked, not hidden). */
export const incompleteDays = (s: HistorySummary): number =>
  Math.max(0, s.days_with_data - s.days_complete) + Math.max(0, s.days - s.days_with_data);
