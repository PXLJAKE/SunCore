// SPDX-License-Identifier: Apache-2.0
import { describe, expect, it } from 'vitest';
import type { PlanInterval, PlanView, RiskView } from '../api/types';
import {
  buildChartData,
  hasIntervals,
  indexAt,
  planTotals,
  plannedActions,
  targetLines,
  windowIntervals,
} from './model';

const T0 = Date.parse('2026-06-21T08:00:00Z');
const iso = (n: number): string => new Date(T0 + n * 900_000).toISOString();

function interval(n: number, over: Partial<PlanInterval> = {}, battery_w = 500): PlanInterval {
  return {
    start: iso(n),
    end: iso(n + 1),
    devices: {},
    battery: { charge_from_surplus: true, discharge_lock: false },
    expected: {
      pv_p10_w: 1000,
      pv_p50_w: 2000,
      pv_p90_w: 3000,
      load_w: 600,
      import_w: 0,
      export_w: 0,
      battery_w,
      soc_pct: 50 + n,
      devices_w: 0,
      curtail_w: 0,
    },
    prices: { import_price: 0.3, export_price: 0.08, estimated: false },
    reasons: [{ key: 'planner.battery.charge_surplus', args: { power_w: 500 } }],
    ...over,
  } as PlanInterval;
}

const view = (intervals: PlanInterval[]): PlanView =>
  ({ plan: { intervals, reasons: [], source: 'solver' } }) as unknown as PlanView;

describe('windowIntervals', () => {
  const v = view(Array.from({ length: 200 }, (_, n) => interval(n)));
  it('drops ended intervals and cuts at the window', () => {
    const now = new Date(T0 + 2 * 900_000 + 1000);
    const w = windowIntervals(v, 24, now);
    expect(w[0]!.start).toBe(iso(2));
    expect(w.length).toBe(96 + 1);
    expect(windowIntervals(v, 48, now).length).toBeGreaterThan(w.length);
  });
  it('is empty without a plan', () => {
    expect(windowIntervals(null, 24, new Date())).toEqual([]);
  });
});

describe('buildChartData', () => {
  it('maps quantiles, flows, SoC and prices per interval', () => {
    const d = buildChartData([interval(0), interval(1, { prices: null, expected: null })]);
    expect(d.start).toEqual([T0, T0 + 900_000]);
    expect(d.endMs).toBe(T0 + 2 * 900_000);
    expect(d.pvP10).toEqual([1000, null]);
    expect(d.pvP50).toEqual([2000, 0]);
    expect(d.socPct).toEqual([50, null]);
    expect(d.importPrice).toEqual([0.3, null]);
    expect(d.reasons[0]![0]!.key).toBe('planner.battery.charge_surplus');
  });
  it('handles an empty plan', () => {
    expect(buildChartData([]).start).toEqual([]);
  });
});

describe('indexAt', () => {
  const start = [10, 20, 30];
  it('finds the containing interval', () => {
    expect(indexAt(start, 5)).toBe(-1);
    expect(indexAt(start, 10)).toBe(0);
    expect(indexAt(start, 29)).toBe(1);
    expect(indexAt(start, 999)).toBe(2);
    expect(indexAt([], 1)).toBe(-1);
  });
});

describe('targetLines', () => {
  it('reads the buffer and the evening target, null without a model', () => {
    const risk = {
      morning_buffer: { soc_pct: 40 },
      evening_target: { soc_pct: 85 },
    } as unknown as RiskView;
    expect(targetLines(risk)).toEqual({ morningPct: 40, eveningPct: 85 });
    expect(targetLines(null)).toEqual({ morningPct: null, eveningPct: null });
  });
});

describe('plannedActions', () => {
  const quiet = (n: number) =>
    interval(n, { reasons: [{ key: 'planner.interval.balanced', args: {} }] });
  const lock = (n: number) =>
    interval(n, {
      battery: { charge_from_surplus: false, discharge_lock: true },
      reasons: [{ key: 'planner.battery.hold_for_evening', args: { soc_pct: 80, time: '19:00' } }],
    });

  it('merges consecutive intervals with the same reasons and keeps the SoC span', () => {
    const list = [interval(0), interval(1), interval(2), lock(3), lock(4)];
    const a = plannedActions(list, new Date(T0), 10);
    expect(a).toHaveLength(2);
    expect(a[0]!.start.toISOString()).toBe(iso(0));
    expect(a[0]!.end.toISOString()).toBe(iso(3));
    expect(a[0]!.socToPct).toBe(52);
    expect(a[0]!.socFromPct).toBeNull();
    expect(a[1]!.dischargeLock).toBe(true);
    expect(a[1]!.socFromPct).toBe(52);
  });
  it('skips quiet intervals and starts a new run after them', () => {
    const list = [interval(0), quiet(1), interval(2)];
    expect(plannedActions(list, new Date(T0), 10)).toHaveLength(2);
  });
  it('skips what is over and respects the limit', () => {
    const list = [interval(0), lock(1), interval(2), lock(3)];
    expect(plannedActions(list, new Date(T0 + 900_000), 10)[0]!.dischargeLock).toBe(true);
    expect(plannedActions(list, new Date(T0), 2)).toHaveLength(2);
  });
  it('is empty when nothing is planned', () => {
    expect(plannedActions([quiet(0)], new Date(T0), 5)).toEqual([]);
  });
});

describe('planTotals', () => {
  it('sums energy over the intervals and tracks the SoC range and the cost', () => {
    const list = [
      interval(0, {}, 500),
      interval(1, {
        expected: {
          pv_p50_w: 0,
          load_w: 1000,
          import_w: 1000,
          export_w: 0,
          battery_w: 0,
          soc_pct: 40,
        },
        battery: { grid_charge_max_w: 0 },
      } as Partial<PlanInterval>),
    ];
    const t = planTotals(list);
    expect(t.hours).toBeCloseTo(0.5);
    expect(t.pvKwh).toBeCloseTo(0.5);
    expect(t.importKwh).toBeCloseTo(0.25);
    expect(t.minSocPct).toBe(40);
    expect(t.maxSocPct).toBe(50);
    expect(t.endSocPct).toBe(40);
    expect(t.netCost).toBeCloseTo(0.25 * 0.3);
  });
  it('counts grid charging only where the plan allows it', () => {
    const grid = interval(
      0,
      {
        battery: { charge_from_surplus: false, discharge_lock: false, grid_charge_max_w: 3000 },
        expected: {
          pv_p50_w: 0,
          load_w: 500,
          import_w: 3500,
          export_w: 0,
          battery_w: 3000,
          soc_pct: 60,
        },
      } as Partial<PlanInterval>,
      3000,
    );
    expect(planTotals([grid]).gridChargeKwh).toBeCloseTo(0.75);
  });
});

describe('hasIntervals', () => {
  it('is false for the default plan', () => {
    expect(hasIntervals(null)).toBe(false);
    expect(hasIntervals(view([]))).toBe(false);
    expect(hasIntervals(view([interval(0)]))).toBe(true);
  });
});
