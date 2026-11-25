// SPDX-License-Identifier: Apache-2.0
// The comparison of two plan snapshots (`GET /plan/compare`) as extra lines on the chart and rows
// of a table. The endpoint lists only intervals that differ, so everything else equals plan A.
import type { IntervalDiff, PlanCompare } from '../api/types';
import type { ChartData } from './model';

export interface CompareSeries {
  /** Planned SoC at the end of every interval of the shown plan, as plan B has it. */
  socPct: (number | null)[];
  /** Planned battery power, + = charging, as plan B has it. */
  batteryW: number[];
}

/** Plan B's SoC and battery power on the intervals of the chart (plan A's where nothing differs). */
export function compareSeries(data: ChartData, diff: PlanCompare): CompareSeries {
  const byStart = new Map<number, IntervalDiff>();
  for (const d of diff.intervals) byStart.set(Date.parse(d.start), d);
  const socPct = data.socPct.slice();
  const batteryW = data.batteryW.slice();
  data.start.forEach((ms, i) => {
    const b = byStart.get(ms)?.b;
    if (!b?.expected) return;
    socPct[i] = b.expected.soc_pct ?? socPct[i] ?? null;
    batteryW[i] = b.expected.battery_w;
  });
  return { socPct, batteryW };
}

/** Rows of the difference table that fall into the window (`only_in` rows included). */
export function compareRows(diff: PlanCompare, fromMs: number, toMs: number): IntervalDiff[] {
  return diff.intervals.filter((d) => Date.parse(d.end) > fromMs && Date.parse(d.start) < toMs);
}

/** Whether a row changed anything the table shows (a pure price change still counts). */
export const hasDelta = (d: IntervalDiff): boolean =>
  d.only_in !== null ||
  [
    d.battery_w_delta,
    d.soc_pct_delta,
    d.import_w_delta,
    d.export_w_delta,
    d.devices_w_delta,
    d.import_price_delta,
  ].some((v) => v !== null && Math.abs(v) > 1e-9) ||
  d.devices_changed.length > 0;
