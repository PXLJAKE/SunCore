// SPDX-License-Identifier: Apache-2.0
// ECharts options of the what-if comparison as pure functions of the result, the theme colours
// and the translated labels. Only types are imported from ECharts (the runtime is registered in
// HistoryChart.svelte, in the chart chunk), so this logic is testable without a canvas.
import type { EChartsCoreOption } from 'echarts/core';
import type { BacktestDay, BacktestResult } from '../api/types';
import { withAlpha } from '../plan/chartOption';
import { axisStyle, axisTooltip, base, type HistoryColors } from '../history/chartOptions';

export interface WhatIfText {
  actual: string;
  whatif: string;
  perfect: string;
  money: (v: number) => string;
  power: (w: number) => string;
  percent: (pct: number) => string;
  /** Axis label and tooltip heading of a day (`2026-06-15`). */
  day: (day: string) => string;
  /** Label of an hour start (an ISO timestamp). */
  when: (iso: string) => string;
}

export interface Ctx {
  colors: HistoryColors;
  text: WhatIfText;
  compact: boolean;
}

type Cell = number | null;

const cents = (v: number): number => Math.round(v * 100) / 100;

/** Net cost per day: actual, what-if and (if replayed) perfect foresight. */
export function dailyCostOption(days: readonly BacktestDay[], c: Ctx): EChartsCoreOption {
  const series: Record<string, unknown>[] = [
    {
      name: c.text.actual,
      type: 'bar',
      itemStyle: { color: c.colors.textSecondary },
      barMaxWidth: 22,
      data: days.map((d) => cents(d.actual.cost_net)),
    },
    {
      name: c.text.whatif,
      type: 'bar',
      itemStyle: { color: c.colors.primary },
      barMaxWidth: 22,
      data: days.map((d) => cents(d.whatif.cost_net)),
    },
  ];
  if (days.some((d) => d.perfect)) {
    series.push({
      name: c.text.perfect,
      type: 'bar',
      itemStyle: {
        color: withAlpha(c.colors.warning, 0.45),
        borderColor: c.colors.warning,
        borderType: 'dashed',
        borderWidth: 1,
      },
      barMaxWidth: 22,
      data: days.map((d) => (d.perfect ? cents(d.perfect.cost_net) : null)),
    });
  }
  return {
    ...base(c),
    tooltip: { trigger: 'axis', formatter: axisTooltip(c, c.text.money, c.text.day) },
    xAxis: {
      type: 'category',
      data: days.map((d) => d.day),
      axisLabel: { color: c.colors.textSecondary, formatter: (v: string) => c.text.day(v) },
    },
    yAxis: {
      type: 'value',
      ...axisStyle(c),
      axisLabel: { color: c.colors.textSecondary, formatter: (v: number) => c.text.money(v) },
    },
    series,
  };
}

type Hourly = BacktestResult['hourly'];

function lines(
  c: Ctx,
  h: Hourly,
  fmt: (v: number) => string,
  pairs: {
    name: string;
    data: readonly Cell[] | null | undefined;
    color: string;
    dash?: boolean;
  }[],
  extra: Record<string, unknown> = {},
): EChartsCoreOption {
  return {
    ...base(c),
    tooltip: { trigger: 'axis', formatter: axisTooltip(c, fmt, c.text.when) },
    xAxis: {
      type: 'category',
      data: h.starts,
      boundaryGap: false,
      axisLabel: { color: c.colors.textSecondary, formatter: (v: string) => c.text.when(v) },
    },
    yAxis: {
      type: 'value',
      ...axisStyle(c),
      ...extra,
      axisLabel: { color: c.colors.textSecondary, formatter: (v: number) => fmt(v) },
    },
    series: pairs
      .filter((p) => p.data)
      .map((p) => ({
        name: p.name,
        type: 'line',
        showSymbol: false,
        connectNulls: false,
        lineStyle: { color: p.color, width: 2, type: p.dash ? 'dashed' : 'solid' },
        itemStyle: { color: p.color },
        data: p.data,
      })),
  };
}

/** State of charge (%) of the actual run, the what-if and (if replayed) perfect foresight. */
export function socOption(h: Hourly, c: Ctx): EChartsCoreOption {
  return lines(
    c,
    h,
    c.text.percent,
    [
      { name: c.text.actual, data: h.actual_soc_pct, color: c.colors.textSecondary },
      { name: c.text.whatif, data: h.whatif_soc_pct, color: c.colors.primary },
      { name: c.text.perfect, data: h.perfect_soc_pct, color: c.colors.warning, dash: true },
    ],
    { min: 0, max: 100 },
  );
}

/** Grid power (+ import) of the actual run and the what-if. */
export function gridOption(h: Hourly, c: Ctx): EChartsCoreOption {
  return lines(c, h, c.text.power, [
    { name: c.text.actual, data: h.actual_grid_w, color: c.colors.textSecondary },
    { name: c.text.whatif, data: h.whatif_grid_w, color: c.colors.primary },
  ]);
}
