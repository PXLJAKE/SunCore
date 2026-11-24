// SPDX-License-Identifier: Apache-2.0
import { describe, expect, it } from 'vitest';
import type { HistoryBucket, HistoryHeatmap, HistorySankey, HistorySkill } from '../api/types';
import {
  costOption,
  energyOption,
  heatmapOption,
  sankeyOption,
  skillOption,
  type Ctx,
  type HistoryText,
} from './chartOptions';

const text: HistoryText = {
  series: {
    pv: 'PV',
    house: 'House',
    import: 'Import',
    export: 'Export',
    costNet: 'Cost',
    baselineNoPv: 'No PV',
    baselinePvOnly: 'PV only',
    cycles: 'Cycles',
    socMin: 'Min',
    socMax: 'Max',
  },
  node: {
    pv: 'PV',
    grid_import: 'Grid in',
    battery_discharge: 'Battery out',
    house: 'House',
    battery_charge: 'Battery in',
    grid_export: 'Grid out',
  },
  incomplete: 'incomplete',
  energy: (wh) => `${wh / 1000} kWh`,
  money: (v) => `${v} EUR`,
  power: (w) => `${w} W`,
  percent: (p) => `${p} %`,
  number: (v) => String(v),
  bucket: (k) => k,
  day: (d) => d,
  weekday: (i) => `d${i}`,
  hour: (h) => `${h}:00`,
  skillName: (s, k, h) => `${s}/${k}/${h}`,
};
const colors = Object.fromEntries(
  [
    'text',
    'textSecondary',
    'divider',
    'card',
    'primary',
    'pv',
    'house',
    'gridImport',
    'gridExport',
    'battery',
    'batteryOut',
    'warning',
  ].map((k) => [k, '#123456']),
) as unknown as Ctx['colors'];
const ctx: Ctx = { colors, text, compact: false };

const bucket = (key: string, over: Partial<HistoryBucket> = {}): HistoryBucket =>
  ({
    key,
    complete: true,
    live: false,
    days_with_data: 1,
    energy: {
      pv_wh: 10_000,
      house_wh: 8000,
      import_wh: 1500,
      export_wh: 3500,
      charge_wh: 0,
      discharge_wh: 0,
    },
    kpis: { autarky: 0.8, self_consumption: 0.6, equivalent_full_cycles: null },
    cost: {
      currency: 'EUR',
      priced_days: 1,
      cost_net: 0.456,
      baseline_no_pv_cost: 2.5,
      baseline_pv_only_cost: 1.2,
      import_cost: null,
      export_revenue: null,
      savings_vs_no_pv: null,
      savings_vs_pv_only: null,
      pv_contribution: null,
    },
    ...over,
  }) as HistoryBucket;

type Series = { name: string; data: unknown[] };
const seriesOf = (o: unknown): Series[] => (o as { series: Series[] }).series;

describe('energyOption', () => {
  it('draws kWh per bucket and dashes incomplete ones', () => {
    const o = energyOption([bucket('2026-06-15'), bucket('2026-06-16', { complete: false })], ctx);
    const pv = seriesOf(o).find((s) => s.name === 'PV')!;
    expect(pv.data[0]).toBe(10);
    expect(pv.data[1]).toMatchObject({ value: 10, itemStyle: { borderType: 'dashed' } });
    expect(seriesOf(o)).toHaveLength(4);
  });
});

describe('costOption', () => {
  it('rounds money and keeps unknown cost as a gap, not zero', () => {
    const o = costOption(
      [
        bucket('2026-06-15'),
        bucket('2026-06-16', {
          cost: { ...bucket('x').cost, cost_net: null, baseline_no_pv_cost: null },
        }),
      ],
      ctx,
    );
    const cost = seriesOf(o).find((s) => s.name === 'Cost')!;
    expect(cost.data[0]).toBe(0.46);
    expect(cost.data[1]).toBeNull();
  });
});

describe('sankeyOption', () => {
  const data: HistorySankey = {
    period: 'day',
    key: '2026-06-15',
    start: '',
    end: '',
    complete: true,
    nodes: ['pv', 'grid_import', 'battery_discharge', 'house', 'battery_charge', 'grid_export'],
    links: [
      { source: 'pv', target: 'house', wh: 5000 },
      { source: 'pv', target: 'grid_export', wh: 2500 },
      { source: 'grid_import', target: 'house', wh: 0 },
    ],
    residual_wh: 0,
  };

  it('leaves out zero flows and the nodes nothing flows through', () => {
    const [s] = seriesOf(sankeyOption(data, ctx)) as unknown as {
      data: { name: string }[];
      links: { source: string; value: number }[];
    }[];
    expect(s!.links).toEqual([
      { source: 'pv', target: 'house', value: 5 },
      { source: 'pv', target: 'grid_export', value: 2.5 },
    ]);
    expect(s!.data.map((n) => n.name).sort()).toEqual(['grid_export', 'house', 'pv']);
  });
});

describe('heatmapOption', () => {
  it('maps weekday rows and hour columns to [hour, weekday, value] cells', () => {
    const values = Array.from({ length: 7 }, (_, d) =>
      Array.from({ length: 24 }, (_, h) => (d === 2 && h === 5 ? 700 : null)),
    );
    const data = {
      key: 'site.house_w',
      unit: 'W',
      values,
      counts: [],
    } as unknown as HistoryHeatmap;
    const o = heatmapOption(data, ctx) as {
      series: { data: unknown[] }[];
      visualMap: { max: number };
    };
    expect(o.series[0]!.data).toHaveLength(7 * 24);
    expect(o.series[0]!.data).toContainEqual([5, 2, 700]);
    expect(o.visualMap.max).toBe(700);
  });
});

describe('skillOption', () => {
  it('aligns every series on the union of the days', () => {
    const data: HistorySkill = {
      start: '2026-06-01',
      end: '2026-06-03',
      series: [
        {
          source_id: 'a',
          kind: 'pv',
          horizon: '0_6h',
          mae_w: 1,
          bias_w: 0,
          n: 2,
          points: [
            { day: '2026-06-01', mae_w: 100, bias_w: 0, n: 1 },
            { day: '2026-06-03', mae_w: 120, bias_w: 0, n: 1 },
          ],
        },
        {
          source_id: 'b',
          kind: 'load',
          horizon: '0_6h',
          mae_w: 1,
          bias_w: 0,
          n: 1,
          points: [{ day: '2026-06-02', mae_w: 50, bias_w: 0, n: 1 }],
        },
      ],
    };
    const o = skillOption(data, ctx);
    const [a, b] = seriesOf(o);
    expect(a!.name).toBe('a/pv/0_6h');
    expect(a!.data).toEqual([100, null, 120]);
    expect(b!.data).toEqual([null, 50, null]);
  });
});
