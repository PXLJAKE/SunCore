// SPDX-License-Identifier: Apache-2.0
import { describe, expect, it } from 'vitest';
import type { HistoryColors } from '../history/chartOptions';
import { figures, result } from './fixtures';
import { dailyCostOption, gridOption, socOption, type Ctx } from './chartOptions';

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
  ].map((k) => [k, `#${k}`]),
) as unknown as HistoryColors;
const ctx: Ctx = {
  colors,
  compact: false,
  text: {
    actual: 'Actual',
    whatif: 'What-if',
    perfect: 'Perfect',
    money: (v) => `${v} EUR`,
    power: (w) => `${w} W`,
    percent: (p) => `${p} %`,
    day: (d) => d,
    when: (iso) => iso,
  },
};
type Series = { name: string; data: unknown[] }[];

describe('dailyCostOption', () => {
  it('draws actual and what-if per day, and perfect foresight only when replayed', () => {
    const r = result();
    const opt = dailyCostOption(r.days, ctx) as { series: Series };
    expect(opt.series.map((s) => s.name)).toEqual(['Actual', 'What-if']);
    expect(opt.series[1]?.data).toEqual([0.3, 0.3]);
    const days = r.days.map((d) => ({ ...d, perfect: figures({ cost_net: 0.1 }) }));
    const withPerfect = dailyCostOption(days, ctx) as { series: Series };
    expect(withPerfect.series.map((s) => s.name)).toEqual(['Actual', 'What-if', 'Perfect']);
  });
});

describe('hourly charts', () => {
  const h = result().hourly;

  it('keeps gaps as null so the line breaks instead of dropping to zero', () => {
    const opt = gridOption(h, ctx) as { series: Series };
    expect(opt.series[0]?.data).toEqual([100, null]);
  });

  it('bounds the state of charge to 0..100 and skips a missing perfect series', () => {
    const opt = socOption(h, ctx) as { series: Series; yAxis: { min: number; max: number } };
    expect(opt.yAxis).toMatchObject({ min: 0, max: 100 });
    expect(opt.series.map((s) => s.name)).toEqual(['Actual', 'What-if']);
    const perfect = socOption({ ...h, perfect_soc_pct: [50, 70] }, ctx) as { series: Series };
    expect(perfect.series).toHaveLength(3);
  });
});
