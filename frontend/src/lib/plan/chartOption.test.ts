// SPDX-License-Identifier: Apache-2.0
import { describe, expect, it } from 'vitest';
import {
  buildOption,
  chartHeight,
  withAlpha,
  type ChartColors,
  type ChartLabels,
} from './chartOption';
import type { ChartData } from './model';

const colors: ChartColors = {
  text: '#111111',
  textSecondary: '#555555',
  divider: '#cccccc',
  card: '#ffffff',
  pv: '#c77700',
  load: '#111111',
  battery: '#00796b',
  devices: '#0b6fa4',
  gridImport: '#2f6fa8',
  gridExport: '#7040b8',
  target: '#8a5100',
  now: '#555555',
  batteryOut: '#c2185b',
};
const labels: ChartLabels = {
  pvBand: 'band',
  pvP50: 'pv',
  load: 'load',
  devices: 'devices',
  batteryPower: 'battery',
  gridImport: 'import',
  gridExport: 'export',
  soc: 'soc',
  importPrice: 'iprice',
  exportPrice: 'eprice',
  morningBuffer: (p) => `morning ${p}`,
  eveningTarget: (p) => `evening ${p}`,
  now: 'now',
  decision: 'decision',
  actualPv: 'a-pv',
  actualHouse: 'a-house',
  actualGrid: 'a-grid',
  actualBattery: 'a-battery',
  actualSoc: 'a-soc',
  comparedSoc: 'c-soc',
  comparedBattery: 'c-battery',
  laneName: (id) => `lane ${id}`,
  power: (w) => `${w} W`,
  percent: (p) => `${p} %`,
  price: (v) => String(v),
  axisTime: (ms) => String(ms),
  tooltip: (i) => {
    const el = document.createElement('div');
    el.textContent = `interval ${i}`;
    return el;
  },
};
const data: ChartData = {
  start: [0, 900_000, 1_800_000],
  endMs: 2_700_000,
  pvP10: [100, 200, null],
  pvP50: [150, 300, 0],
  pvP90: [200, 400, null],
  loadW: [50, 60, 70],
  devicesW: [0, 0, 0],
  batteryW: [10, -20, 0],
  importW: [0, 5, 0],
  exportW: [3, 0, 0],
  socPct: [50, 55, null],
  importPrice: [0.3, 0.31, 0.32],
  exportPrice: [0.08, 0.08, 0.08],
  reasons: [[], [], []],
};

type Series = {
  name: string;
  type: string;
  data: unknown[];
  step?: string;
  markLine?: { data: unknown[] };
};
const build = (over: Partial<Parameters<typeof buildOption>[0]> = {}) =>
  buildOption({
    data,
    lines: { morningPct: 30, eveningPct: 80 },
    nowMs: 900_000,
    colors,
    labels,
    compact: false,
    ...over,
  }) as unknown as {
    series: Series[];
    grid: unknown[];
    legend: { data: string[] };
    tooltip: { formatter: (p: unknown) => HTMLElement };
  };

describe('buildOption', () => {
  it('draws the band as stacked P10 and P90-P10, with P50 on top', () => {
    const o = build();
    const low = o.series.find((s) => s.name === 'band (low)')!;
    const band = o.series.find((s) => s.name === 'band')!;
    expect(low.data).toEqual([
      [0, 100],
      [900_000, 200],
      [1_800_000, null],
    ]);
    expect(band.data).toEqual([
      [0, 100],
      [900_000, 200],
      [1_800_000, null],
    ]);
    expect(o.series.find((s) => s.name === 'pv')!.data).toHaveLength(3);
  });
  it('closes step lines at the end of the plan and negates export', () => {
    const o = build();
    const battery = o.series.find((s) => s.name === 'battery')!;
    expect(battery.step).toBe('start');
    expect(battery.data.at(-1)).toEqual([2_700_000, 0]);
    expect(o.series.find((s) => s.name === 'export')!.data[0]).toEqual([0, -3]);
  });
  it('plots the SoC at the end of each interval with the target lines', () => {
    const o = build();
    const soc = o.series.find((s) => s.name === 'soc')!;
    expect(soc.data[0]).toEqual([900_000, 50]);
    const names = (soc.markLine!.data as { name: string }[]).map((d) => d.name);
    expect(names).toEqual(['morning 30', 'evening 80']);
    expect(
      build({ lines: { morningPct: null, eveningPct: null } }).series.find((s) => s.name === 'soc')!
        .markLine!.data,
    ).toEqual([]);
  });
  it('shows the now line only inside the plan', () => {
    const inside = build().series.find((s) => s.name === 'pv')!.markLine!.data;
    expect(inside).toHaveLength(1);
    const outside = build({ nowMs: 99_000_000 }).series.find((s) => s.name === 'pv')!.markLine!
      .data;
    expect(outside).toEqual([]);
  });
  it('lists every visible series in the legend and builds the tooltip from the axis value', () => {
    const o = build();
    expect(o.legend.data).toHaveLength(10);
    expect(o.series.find((s) => s.name === 'devices')!.step).toBe('start');
    expect(o.tooltip.formatter([{ axisValue: 1_000_000 }]).textContent).toBe('interval 1');
    expect(o.tooltip.formatter({}).textContent).toBe('interval 0');
  });
  it('has room for the legend on narrow screens', () => {
    expect(chartHeight(true)).toBeGreaterThan(chartHeight(false) - 80);
    expect(build({ compact: true }).grid).toHaveLength(3);
  });
});

describe('buildOption extras', () => {
  const actual = {
    times: [0, 900_000],
    stepMs: 900_000,
    pvW: [1, 2],
    houseW: [3, 4],
    gridW: [5, 6],
    batteryW: [7, 8],
    socPct: [50, 51],
    devices: {},
  };
  const lanes = [
    {
      id: 'battery',
      battery: true,
      planned: [{ fromMs: 0, toMs: 900_000, kind: 'charge' as const }],
      actual: [],
    },
  ];
  it('adds measured lines, a Gantt panel with a shared zoom and the compare lines', () => {
    const o = build({
      actual,
      lanes,
      compare: { socPct: [1, 2, 3], batteryW: [1, 2, 3] },
      window: { fromMs: -1000, toMs: 5_000_000 },
      marker: { atMs: 900_000, fromMs: 900_000, toMs: 1_800_000 },
    }) as unknown as {
      series: Series[];
      grid: unknown[];
      xAxis: { min: number; max: number }[];
      dataZoom: { xAxisIndex: number[] }[];
      legend: { data: string[] };
    };
    expect(o.legend.data).toHaveLength(17);
    expect(o.series.some((s) => s.name === 'a-pv')).toBe(true);
    expect(o.series.filter((s) => s.type === 'custom')).toHaveLength(2);
    expect(o.grid).toHaveLength(4);
    expect(o.dataZoom[0]!.xAxisIndex).toEqual([0, 1, 2, 3]);
    expect(o.xAxis[0]).toMatchObject({ min: -1000, max: 5_000_000 });
    expect(o.series.find((s) => s.name === 'pv')!.markLine!.data).toHaveLength(2);
    expect(chartHeight(false, 17, 1)).toBeGreaterThan(chartHeight(false));
  });
});

describe('withAlpha', () => {
  it('converts hex and rgb colours and leaves others alone', () => {
    expect(withAlpha('#c77700', 0.5)).toBe('rgba(199, 119, 0, 0.5)');
    expect(withAlpha('#fff', 0.1)).toBe('rgba(255, 255, 255, 0.1)');
    expect(withAlpha('rgb(1, 2, 3)', 0.2)).toBe('rgba(1, 2, 3, 0.2)');
    expect(withAlpha('red', 0.2)).toBe('red');
  });
});
