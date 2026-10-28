// SPDX-License-Identifier: Apache-2.0
// Pure helpers for the plan view: the plan's intervals as chart series, the "next actions" list and
// the key numbers of the first-plan preview. No locale, no DOM, no ECharts: unit-testable.
import type { PlanInterval, PlanView, Reason, RiskView } from '../api/types';

export type HoursWindow = 24 | 48;

const MS_PER_HOUR = 3_600_000;

/** Intervals that have not ended yet and start within `hours` of `now`. */
export function windowIntervals(
  view: Pick<PlanView, 'plan'> | null,
  hours: HoursWindow,
  now: Date,
): PlanInterval[] {
  const from = now.getTime();
  const to = from + hours * MS_PER_HOUR;
  return (view?.plan.intervals ?? []).filter(
    (i) => Date.parse(i.end) > from && Date.parse(i.start) < to,
  );
}

/** Chart series: one value per interval (`null` where the plan has none). */
export interface ChartData {
  /** Interval start (ms since epoch). */
  start: number[];
  /** Interval end of the last interval, to close the step lines. */
  endMs: number;
  pvP10: (number | null)[];
  pvP50: number[];
  pvP90: (number | null)[];
  loadW: number[];
  devicesW: number[];
  /** Battery power, + = charging. */
  batteryW: number[];
  importW: number[];
  /** Export as a positive magnitude (the chart negates it to draw it below the axis). */
  exportW: number[];
  /** Planned state of charge at the end of the interval. */
  socPct: (number | null)[];
  importPrice: (number | null)[];
  exportPrice: (number | null)[];
  reasons: Reason[][];
}

export function buildChartData(intervals: readonly PlanInterval[]): ChartData {
  const data: ChartData = {
    start: [],
    endMs: intervals.length > 0 ? Date.parse(intervals[intervals.length - 1]!.end) : 0,
    pvP10: [],
    pvP50: [],
    pvP90: [],
    loadW: [],
    devicesW: [],
    batteryW: [],
    importW: [],
    exportW: [],
    socPct: [],
    importPrice: [],
    exportPrice: [],
    reasons: [],
  };
  for (const i of intervals) {
    const e = i.expected;
    data.start.push(Date.parse(i.start));
    data.pvP10.push(e?.pv_p10_w ?? null);
    data.pvP50.push(e?.pv_p50_w ?? 0);
    data.pvP90.push(e?.pv_p90_w ?? null);
    data.loadW.push(e?.load_w ?? 0);
    data.devicesW.push(e?.devices_w ?? 0);
    data.batteryW.push(e?.battery_w ?? 0);
    data.importW.push(e?.import_w ?? 0);
    data.exportW.push(e?.export_w ?? 0);
    data.socPct.push(e?.soc_pct ?? null);
    data.importPrice.push(i.prices?.import_price ?? null);
    data.exportPrice.push(i.prices?.export_price ?? null);
    data.reasons.push(i.reasons ?? []);
  }
  return data;
}

/** Index of the interval that contains `ms` (or the nearest earlier one); `-1` before the first. */
export function indexAt(start: readonly number[], ms: number): number {
  let lo = 0;
  let hi = start.length - 1;
  let found = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (start[mid]! <= ms) {
      found = mid;
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }
  return found;
}

/** Horizontal lines of the SoC panel: the morning buffer and the evening target. */
export interface TargetLines {
  morningPct: number | null;
  eveningPct: number | null;
}

export function targetLines(risk: RiskView | null): TargetLines {
  return {
    morningPct: risk?.morning_buffer?.soc_pct ?? null,
    eveningPct: risk?.evening_target?.soc_pct ?? null,
  };
}

// --- next actions ------------------------------------------------------------------------

/** Reason keys that describe the absence of an action. */
const NO_ACTION = new Set(['planner.interval.balanced']);

export interface PlannedAction {
  start: Date;
  end: Date;
  /** Reasons of the first interval of the run (the numbers differ slightly within a run). */
  reasons: Reason[];
  /** The first reason: what the plan does. */
  headline: Reason | null;
  socFromPct: number | null;
  socToPct: number | null;
  /** Expected mean flows of the first interval of the run. */
  flows: PlanInterval['expected'];
  gridChargeW: number | null;
  dischargeLock: boolean;
}

function signature(i: PlanInterval): string {
  const keys = (i.reasons ?? []).map((r) => r.key).join('|');
  const b = i.battery;
  return `${keys}#${b?.discharge_lock ? 'L' : ''}${(b?.grid_charge_max_w ?? 0) > 0 ? 'G' : ''}`;
}

/**
 * The plan from `now` on as runs of consecutive intervals with the same reasons. Intervals
 * without anything to say ("balanced") are skipped; at most `limit` runs are returned.
 */
export function plannedActions(
  intervals: readonly PlanInterval[],
  now: Date,
  limit: number,
): PlannedAction[] {
  const out: PlannedAction[] = [];
  let lastSig: string | null = null;
  let lastEnd = 0;
  for (const i of intervals) {
    if (Date.parse(i.end) <= now.getTime()) continue;
    const reasons = i.reasons ?? [];
    const quiet = reasons.length === 0 || reasons.every((r) => NO_ACTION.has(r.key));
    if (quiet) {
      lastSig = null;
      continue;
    }
    const sig = signature(i);
    const start = Date.parse(i.start);
    const current = out[out.length - 1];
    if (current && sig === lastSig && start <= lastEnd) {
      current.end = new Date(i.end);
      current.socToPct = i.expected?.soc_pct ?? current.socToPct;
    } else {
      if (out.length >= limit) break;
      out.push({
        start: new Date(i.start),
        end: new Date(i.end),
        reasons: [...reasons],
        headline: reasons[0] ?? null,
        socFromPct: null,
        socToPct: i.expected?.soc_pct ?? null,
        flows: i.expected ?? null,
        gridChargeW: i.battery?.grid_charge_max_w ?? null,
        dischargeLock: i.battery?.discharge_lock ?? false,
      });
    }
    lastSig = sig;
    lastEnd = Date.parse(i.end);
  }
  // The SoC a run starts from is the SoC at the end of the interval before it.
  for (const a of out) {
    const before = intervals.filter((i) => Date.parse(i.end) <= a.start.getTime()).at(-1);
    a.socFromPct = before?.expected?.soc_pct ?? null;
  }
  return out;
}

// --- key numbers (first plan preview) ------------------------------------------------------

export interface PlanTotals {
  hours: number;
  pvKwh: number;
  loadKwh: number;
  importKwh: number;
  exportKwh: number;
  /** Planned SoC at the end of the window and its extremes; `null` without a battery. */
  endSocPct: number | null;
  minSocPct: number | null;
  maxSocPct: number | null;
  /** Energy bought from the grid into the battery in the window (kWh). */
  gridChargeKwh: number;
  /** Planned cost of the grid balance in the window (currency; negative = earning). */
  netCost: number | null;
}

function hoursOf(i: PlanInterval): number {
  return Math.max(0, (Date.parse(i.end) - Date.parse(i.start)) / MS_PER_HOUR);
}

export function planTotals(intervals: readonly PlanInterval[]): PlanTotals {
  const t: PlanTotals = {
    hours: 0,
    pvKwh: 0,
    loadKwh: 0,
    importKwh: 0,
    exportKwh: 0,
    endSocPct: null,
    minSocPct: null,
    maxSocPct: null,
    gridChargeKwh: 0,
    netCost: null,
  };
  for (const i of intervals) {
    const h = hoursOf(i);
    t.hours += h;
    const e = i.expected;
    if (!e) continue;
    t.pvKwh += (e.pv_p50_w * h) / 1000;
    t.loadKwh += ((e.load_w + (e.devices_w ?? 0)) * h) / 1000;
    t.importKwh += (e.import_w * h) / 1000;
    t.exportKwh += (e.export_w * h) / 1000;
    if (e.soc_pct != null) {
      t.endSocPct = e.soc_pct;
      t.minSocPct = t.minSocPct == null ? e.soc_pct : Math.min(t.minSocPct, e.soc_pct);
      t.maxSocPct = t.maxSocPct == null ? e.soc_pct : Math.max(t.maxSocPct, e.soc_pct);
    }
    const limit = i.battery?.grid_charge_max_w ?? 0;
    if (limit > 0 && e.battery_w > 0) {
      t.gridChargeKwh += (Math.min(e.import_w, e.battery_w) * h) / 1000;
    }
    if (i.prices) {
      t.netCost =
        (t.netCost ?? 0) +
        (e.import_w * h * i.prices.import_price) / 1000 -
        (e.export_w * h * i.prices.export_price) / 1000;
    }
  }
  return t;
}

/** A plan can be shown when it holds intervals (the default plan has none). */
export const hasIntervals = (view: PlanView | null): boolean =>
  (view?.plan.intervals.length ?? 0) > 0;
