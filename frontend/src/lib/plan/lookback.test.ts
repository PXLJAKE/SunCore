// SPDX-License-Identifier: Apache-2.0
import { describe, expect, it } from 'vitest';
import type {
  HistorySeries,
  IntervalDiff,
  PlanCompare,
  PlanInterval,
  PlanView,
} from '../api/types';
import { buildActual, actualIndex } from './actual';
import { chartIntervals, needsSnapshot, referenceTime, stitch } from './assemble';
import { compareRows, compareSeries, hasDelta } from './compare';
import { buildLanes, planDeviceIds, segAt } from './gantt';
import type { ChartData } from './model';
import { PAST_MS, parseTarget, parseTime, targetHash, targetWindow } from './target';
import { applyZoom, FULL, isZoomed, visibleSpan, zoomKey } from './zoom';
import { dayInZone, localInputValue, parseLocalInput, zonedMs } from './zone';

const BERLIN = 'Europe/Berlin';
const Q = 900_000;

function interval(startMs: number, over: Partial<PlanInterval> = {}): PlanInterval {
  return {
    start: new Date(startMs).toISOString(),
    end: new Date(startMs + Q).toISOString(),
    battery: null,
    reasons: [],
    ...over,
  } as PlanInterval;
}

describe('zone', () => {
  it('maps wall clock to instants, also on DST days', () => {
    expect(new Date(zonedMs('2026-06-21', '09:30', BERLIN)).toISOString()).toBe(
      '2026-06-21T07:30:00.000Z',
    );
    // 2026-03-29 has 23 hours in Berlin.
    const span = zonedMs('2026-03-30', '00:00', BERLIN) - zonedMs('2026-03-29', '00:00', BERLIN);
    expect(span).toBe(23 * 3_600_000);
    const long = zonedMs('2026-10-26', '00:00', BERLIN) - zonedMs('2026-10-25', '00:00', BERLIN);
    expect(long).toBe(25 * 3_600_000);
  });
  it('reads and writes datetime-local values in the site zone', () => {
    const ms = Date.UTC(2026, 5, 21, 7, 30);
    expect(localInputValue(ms, BERLIN)).toBe('2026-06-21T09:30');
    expect(parseLocalInput('2026-06-21T09:30', BERLIN)).toBe(ms);
    expect(parseLocalInput('nonsense', BERLIN)).toBeNull();
    expect(dayInZone(Date.UTC(2026, 5, 20, 22, 30), BERLIN)).toBe('2026-06-21');
  });
});

describe('target', () => {
  const q = (s: string) => new URLSearchParams(s);
  it('reads at and day links, at wins, invalid falls back', () => {
    expect(parseTarget(q('day=2026-06-20'), 24, BERLIN)).toEqual({
      kind: 'day',
      day: '2026-06-20',
    });
    const at = parseTarget(q('at=2026-06-20T12:00:00%2B02:00&day=2026-06-01'), 24, BERLIN);
    expect(at).toEqual({ kind: 'at', atMs: Date.UTC(2026, 5, 20, 10) });
    expect(parseTarget(q('at=junk&day=2026-13-99'), 48, BERLIN)).toEqual({
      kind: 'live',
      hours: 48,
    });
  });
  it('takes a time without offset as site time', () => {
    expect(parseTime('2026-06-20T12:00:00', BERLIN)).toBe(Date.UTC(2026, 5, 20, 10));
    expect(parseTime(null, BERLIN)).toBeNull();
  });
  it('builds windows: live with past, whole day, around a decision', () => {
    const now = Date.UTC(2026, 5, 21, 7, 40);
    const live = targetWindow({ kind: 'live', hours: 24 }, now, BERLIN);
    expect(live.toMs).toBe(now + 24 * 3_600_000);
    expect(now - live.fromMs).toBeGreaterThanOrEqual(PAST_MS);
    expect(live.fromMs % Q).toBe(0);
    const day = targetWindow({ kind: 'day', day: '2026-06-20' }, now, BERLIN);
    expect(day.toMs - day.fromMs).toBe(24 * 3_600_000);
    const at = targetWindow({ kind: 'at', atMs: now }, now, BERLIN);
    expect(at.fromMs).toBeLessThanOrEqual(now - PAST_MS);
    expect(at.toMs).toBeGreaterThan(now);
  });
  it('writes hashes the router reads back', () => {
    expect(targetHash({ kind: 'live', hours: 24 })).toBe('#/plan');
    expect(targetHash({ kind: 'day', day: '2026-06-20' })).toBe('#/plan?day=2026-06-20');
    const h = targetHash({ kind: 'at', atMs: 0 }, Q);
    expect(h).toContain('at=1970-01-01T00%3A00%3A00.000Z');
    expect(h).toContain('compare=');
  });
});

describe('assemble', () => {
  const live = { plan: { intervals: [interval(10 * Q), interval(11 * Q)] } } as unknown as Pick<
    PlanView,
    'plan'
  >;
  const earlier = [interval(8 * Q), interval(9 * Q), interval(10 * Q)];
  it('completes the live plan with the snapshot before it', () => {
    expect(stitch(earlier, live.plan.intervals).map((i) => Date.parse(i.start) / Q)).toEqual([
      8, 9, 10, 11,
    ]);
    const win = { fromMs: 8 * Q, toMs: 12 * Q };
    expect(chartIntervals({ kind: 'live', hours: 24 }, win, live, earlier)).toHaveLength(4);
    expect(chartIntervals({ kind: 'at', atMs: 0 }, win, live, earlier)).toHaveLength(3);
  });
  it('asks for a snapshot only when the live plan does not cover the window', () => {
    const t = { kind: 'live', hours: 24 } as const;
    expect(needsSnapshot(t, { fromMs: 9 * Q, toMs: 20 * Q }, live)).toBe(true);
    expect(needsSnapshot(t, { fromMs: 10 * Q, toMs: 20 * Q }, live)).toBe(false);
    expect(needsSnapshot({ kind: 'at', atMs: 0 }, { fromMs: 10 * Q, toMs: 20 * Q }, live)).toBe(
      true,
    );
    expect(referenceTime({ kind: 'at', atMs: 5 }, { fromMs: 0, toMs: 10 }, 99)).toBe(5);
  });
});

describe('actual and gantt', () => {
  const series = (keys: string[], values: (number | null)[][]): HistorySeries =>
    ({
      start: new Date(8 * Q).toISOString(),
      end: new Date(12 * Q).toISOString(),
      resolution: '15m',
      resolution_reason: 'requested',
      times: [8, 9, 10, 11].map((n) => new Date(n * Q).toISOString()),
      series: keys.map((key, i) => ({ key, unit: 'W', values: values[i]! })),
    }) as HistorySeries;

  it('aligns site and device series and finds steps', () => {
    const site = series(
      ['site.pv_w', 'site.battery_w'],
      [
        [1, 2, 3, 4],
        [0, 100, -100, 0],
      ],
    );
    const dev = series(['device.ac.on'], [[0, 1, 1, 0.2]]);
    const a = buildActual(site, dev, ['ac'])!;
    expect(a.pvW).toEqual([1, 2, 3, 4]);
    expect(a.houseW).toEqual([null, null, null, null]);
    expect(a.devices.ac).toEqual([0, 1, 1, 0.2]);
    expect(actualIndex(a, 9 * Q + 5)).toBe(1);
    expect(actualIndex(a, 12 * Q)).toBe(-1);
    expect(buildActual(null, null, [])).toBeNull();
  });

  it('builds planned and measured lanes with merged segments', () => {
    const intervals = [8, 9, 10, 11].map((n, i) =>
      interval(n * Q, {
        devices: { ac: { allowed: i < 3, preference: i === 0 ? 'neutral' : 'prefer_on' } },
        expected: { battery_w: i === 1 ? 500 : i === 2 ? -500 : 0 } as never,
        battery: { discharge_lock: i === 3 } as never,
      }),
    );
    const site = series(['site.battery_w'], [[0, 100, -100, 0]]);
    const dev = series(['device.ac.on'], [[0, 1, 1, 0.2]]);
    const a = buildActual(site, dev, ['ac']);
    expect(planDeviceIds(intervals)).toEqual(['ac']);
    const lanes = buildLanes(intervals, a);
    expect(lanes.map((l) => l.id)).toEqual(['battery', 'ac']);
    const [battery, ac] = lanes as [(typeof lanes)[0], (typeof lanes)[0]];
    expect(battery.planned.map((s) => s.kind)).toEqual(['charge', 'discharge', 'lock']);
    expect(battery.actual.map((s) => s.kind)).toEqual(['charge', 'discharge']);
    expect(ac.planned.map((s) => [s.kind, s.fromMs / Q, s.toMs / Q])).toEqual([
      ['allowed', 8, 9],
      ['prefer_on', 9, 11],
    ]);
    expect(ac.actual.map((s) => s.kind)).toEqual(['run', 'partial']);
    expect(segAt(ac.planned, 10 * Q)?.kind).toBe('prefer_on');
    expect(segAt(ac.planned, 11 * Q)).toBeNull();
    expect(buildLanes([interval(0)], null)).toEqual([]);
  });
});

describe('compare', () => {
  const data = {
    start: [0, Q],
    socPct: [50, 60],
    batteryW: [10, 20],
  } as ChartData;
  const diff = (over: Partial<IntervalDiff>): IntervalDiff =>
    ({
      start: new Date(Q).toISOString(),
      end: new Date(2 * Q).toISOString(),
      only_in: null,
      a: null,
      b: null,
      battery_w_delta: null,
      soc_pct_delta: null,
      import_w_delta: null,
      export_w_delta: null,
      devices_w_delta: null,
      import_price_delta: null,
      devices_changed: [],
      ...over,
    }) as IntervalDiff;

  it('uses plan B where it differs and plan A elsewhere', () => {
    const d = diff({ b: interval(Q, { expected: { soc_pct: 80, battery_w: -5 } as never }) });
    const out = compareSeries(data, { intervals: [d] } as PlanCompare);
    expect(out.socPct).toEqual([50, 80]);
    expect(out.batteryW).toEqual([10, -5]);
  });
  it('filters rows to the window and drops rows without change', () => {
    const rows = compareRows(
      {
        intervals: [diff({}), diff({ start: '2000-01-01T00:00:00Z', end: '2000-01-01T00:15:00Z' })],
      } as PlanCompare,
      Q,
      3 * Q,
    );
    expect(rows).toHaveLength(1);
    expect(hasDelta(diff({}))).toBe(false);
    expect(hasDelta(diff({ import_price_delta: 0.01 }))).toBe(true);
    expect(hasDelta(diff({ only_in: 'a' }))).toBe(true);
  });
});

describe('zoom', () => {
  it('zooms around the centre and clamps', () => {
    const z = applyZoom(FULL, 'in');
    expect(z.end - z.start).toBeCloseTo(66.67, 1);
    expect(applyZoom(FULL, 'out')).toEqual(FULL);
    expect(applyZoom({ start: 0, end: 50 }, 'left')).toEqual({ start: 0, end: 50 });
    expect(applyZoom({ start: 0, end: 50 }, 'right')).toEqual({ start: 12.5, end: 62.5 });
    expect(
      applyZoom({ start: 40, end: 42 }, 'in').end - applyZoom({ start: 40, end: 42 }, 'in').start,
    ).toBe(4);
    expect(applyZoom({ start: 10, end: 20 }, 'reset')).toEqual(FULL);
  });
  it('maps keys and reports the visible span', () => {
    expect(zoomKey('+')).toBe('in');
    expect(zoomKey('-')).toBe('out');
    expect(zoomKey('ArrowLeft')).toBe('left');
    expect(zoomKey('0')).toBe('reset');
    expect(zoomKey('a')).toBeNull();
    expect(isZoomed(FULL)).toBe(false);
    expect(isZoomed({ start: 10, end: 100 })).toBe(true);
    expect(visibleSpan(0, 1000, { start: 10, end: 60 })).toEqual([100, 600]);
  });
});
