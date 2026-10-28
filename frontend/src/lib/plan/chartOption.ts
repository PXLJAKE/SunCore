// SPDX-License-Identifier: Apache-2.0
// The ECharts option of the plan chart as a pure function of the plan data, the theme colours and
// the translated labels. No ECharts runtime is imported here (types only), so the logic is
// unit-testable without a canvas and stays out of the first-paint bundle.
import type { EChartsCoreOption } from 'echarts/core';
import type { ChartData, TargetLines } from './model';
import { indexAt } from './model';

/** Theme colours, read from the CSS variables (HA names) of the chart's container. */
export interface ChartColors {
  text: string;
  textSecondary: string;
  divider: string;
  card: string;
  pv: string;
  load: string;
  battery: string;
  gridImport: string;
  gridExport: string;
  target: string;
  now: string;
}

/** Everything the chart says, already translated and formatted for the active locale. */
export interface ChartLabels {
  pvBand: string;
  pvP50: string;
  load: string;
  batteryPower: string;
  gridImport: string;
  gridExport: string;
  soc: string;
  importPrice: string;
  exportPrice: string;
  morningBuffer: (pct: number) => string;
  eveningTarget: (pct: number) => string;
  now: string;
  power: (w: number) => string;
  percent: (pct: number) => string;
  price: (value: number) => string;
  axisTime: (ms: number) => string;
  /** DOM content of the tooltip for the interval at `index`. */
  tooltip: (index: number) => HTMLElement;
}

export interface ChartInput {
  data: ChartData;
  lines: TargetLines;
  nowMs: number;
  colors: ChartColors;
  labels: ChartLabels;
  /** Narrow screens need more room for the legend. */
  compact: boolean;
}

/** Colour with alpha from a CSS colour string (`#rrggbb`, `#rgb` or `rgb()`); unchanged if unknown. */
export function withAlpha(color: string, alpha: number): string {
  const c = color.trim();
  const hex = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(c);
  if (hex) {
    const h = hex[1]!.length === 3 ? [...hex[1]!].map((x) => x + x).join('') : hex[1]!;
    const [r, g, b] = [0, 2, 4].map((i) => Number.parseInt(h.slice(i, i + 2), 16));
    return `rgba(${r}, ${g}, ${b}, ${alpha})`;
  }
  const rgb = /^rgba?\(\s*(\d+)[\s,]+(\d+)[\s,]+(\d+)/.exec(c);
  return rgb ? `rgba(${rgb[1]}, ${rgb[2]}, ${rgb[3]}, ${alpha})` : c;
}

type Point = [number, number | null];

const points = (start: readonly number[], values: readonly (number | null)[]): Point[] =>
  start.map((ms, i) => [ms, values[i] ?? null]);

export function buildOption(input: ChartInput): EChartsCoreOption {
  const { data, lines, nowMs, colors: c, labels: l, compact } = input;
  const start = data.start;
  // A step line needs a closing point at the end of the last interval.
  const closed = (values: readonly (number | null)[]): Point[] => {
    const pts = points(start, values);
    if (start.length > 0) pts.push([data.endMs, values[values.length - 1] ?? null]);
    return pts;
  };
  const band: Point[] = points(
    start,
    data.pvP10.map((lo, i) => {
      const hi = data.pvP90[i];
      return lo != null && hi != null ? Math.max(0, hi - lo) : null;
    }),
  );
  const axisLabel = { color: c.textSecondary, fontSize: 11, hideOverlap: true };
  const legendTop = 0;
  const gridTop = compact ? 92 : 56;
  const grids = [
    { left: 52, right: compact ? 12 : 56, top: gridTop, height: compact ? 190 : 230 },
    { left: 52, right: compact ? 12 : 56, top: gridTop + (compact ? 190 : 230) + 24, height: 90 },
    {
      left: 52,
      right: compact ? 12 : 56,
      top: gridTop + (compact ? 190 : 230) + 24 + 90 + 24,
      height: 80,
    },
  ];
  const xAxis = (gridIndex: number, show: boolean) => ({
    type: 'time' as const,
    gridIndex,
    min: start[0],
    max: data.endMs,
    axisLine: { lineStyle: { color: c.divider } },
    splitLine: { show: true, lineStyle: { color: c.divider, opacity: 0.5 } },
    axisTick: { show },
    axisLabel: { ...axisLabel, show, formatter: (v: number) => l.axisTime(v) },
    axisPointer: { show: true, lineStyle: { color: c.textSecondary } },
  });
  const yAxis = (gridIndex: number, format: (v: number) => string, extra: object = {}) => ({
    type: 'value' as const,
    gridIndex,
    axisLine: { show: false },
    axisTick: { show: false },
    axisLabel: { ...axisLabel, formatter: format },
    splitLine: { lineStyle: { color: c.divider, opacity: 0.5 } },
    ...extra,
  });
  const nowLine = {
    silent: true,
    symbol: 'none',
    animation: false,
    lineStyle: { color: c.now, type: 'solid' as const, width: 1 },
    label: {
      show: true,
      formatter: l.now,
      color: c.textSecondary,
      fontSize: 11,
      position: 'insideStartTop' as const,
    },
    data: nowMs >= (start[0] ?? 0) && nowMs <= data.endMs ? [{ xAxis: nowMs }] : [],
  };
  const targetLine = (value: number | null, name: string) =>
    value == null
      ? []
      : [
          {
            yAxis: value,
            name,
            label: {
              show: true,
              formatter: name,
              position: 'insideEndTop' as const,
              color: c.text,
              fontSize: 11,
            },
          },
        ];
  const targetLines = {
    silent: true,
    symbol: 'none',
    animation: false,
    lineStyle: { color: c.target, type: 'dashed' as const, width: 1.5 },
    data: [
      ...targetLine(
        lines.morningPct,
        lines.morningPct == null ? '' : l.morningBuffer(lines.morningPct),
      ),
      ...targetLine(
        lines.eveningPct,
        lines.eveningPct == null ? '' : l.eveningTarget(lines.eveningPct),
      ),
    ],
  };

  return {
    animation: false,
    backgroundColor: 'transparent',
    textStyle: { color: c.text },
    legend: {
      top: legendTop,
      left: 0,
      itemWidth: 16,
      itemHeight: 8,
      itemGap: 10,
      textStyle: { color: c.text, fontSize: 12 },
      inactiveColor: c.textSecondary,
      data: [
        l.pvBand,
        l.pvP50,
        l.load,
        l.batteryPower,
        l.gridImport,
        l.gridExport,
        l.soc,
        l.importPrice,
        l.exportPrice,
      ],
    },
    tooltip: {
      trigger: 'axis',
      triggerOn: 'mousemove|click',
      confine: true,
      enterable: false,
      transitionDuration: 0,
      backgroundColor: c.card,
      borderColor: c.divider,
      textStyle: { color: c.text, fontSize: 12 },
      axisPointer: { type: 'line', lineStyle: { color: c.textSecondary } },
      extraCssText: 'max-width: min(26rem, 90vw); white-space: normal;',
      formatter: (params: unknown): HTMLElement => {
        const first = (Array.isArray(params) ? params[0] : params) as
          { axisValue?: number } | undefined;
        const ms = typeof first?.axisValue === 'number' ? first.axisValue : (start[0] ?? 0);
        return l.tooltip(Math.max(0, indexAt(start, ms)));
      },
    },
    axisPointer: { link: [{ xAxisIndex: 'all' }] },
    dataZoom: [
      {
        type: 'inside',
        xAxisIndex: [0, 1, 2],
        filterMode: 'none',
        zoomOnMouseWheel: 'ctrl',
        moveOnMouseMove: true,
        moveOnMouseWheel: false,
        preventDefaultMouseMove: false,
      },
    ],
    grid: grids,
    xAxis: [xAxis(0, false), xAxis(1, false), xAxis(2, true)],
    yAxis: [
      yAxis(0, (v) => l.power(v)),
      yAxis(1, (v) => l.percent(v), { min: 0, max: 100, interval: 25 }),
      yAxis(2, (v) => l.price(v), { scale: true }),
    ],
    series: [
      {
        name: `${l.pvBand} (low)`,
        type: 'line',
        xAxisIndex: 0,
        yAxisIndex: 0,
        data: points(start, data.pvP10),
        stack: 'pv-band',
        symbol: 'none',
        lineStyle: { opacity: 0 },
        silent: true,
        tooltip: { show: false },
      },
      {
        name: l.pvBand,
        type: 'line',
        xAxisIndex: 0,
        yAxisIndex: 0,
        data: band,
        stack: 'pv-band',
        symbol: 'none',
        lineStyle: { opacity: 0 },
        areaStyle: { color: withAlpha(c.pv, 0.25) },
        itemStyle: { color: withAlpha(c.pv, 0.45) },
        silent: true,
      },
      {
        name: l.pvP50,
        type: 'line',
        xAxisIndex: 0,
        yAxisIndex: 0,
        data: points(start, data.pvP50),
        symbol: 'none',
        lineStyle: { color: c.pv, width: 2 },
        itemStyle: { color: c.pv },
        markLine: nowLine,
      },
      {
        name: l.load,
        type: 'line',
        xAxisIndex: 0,
        yAxisIndex: 0,
        data: points(start, data.loadW),
        symbol: 'none',
        step: 'start',
        lineStyle: { color: c.load, width: 2, type: 'dashed' },
        itemStyle: { color: c.load },
      },
      {
        name: l.batteryPower,
        type: 'line',
        xAxisIndex: 0,
        yAxisIndex: 0,
        data: closed(data.batteryW),
        symbol: 'none',
        step: 'start',
        lineStyle: { color: c.battery, width: 2 },
        itemStyle: { color: c.battery },
      },
      {
        name: l.gridImport,
        type: 'bar',
        xAxisIndex: 0,
        yAxisIndex: 0,
        data: points(start, data.importW),
        barGap: '-100%',
        barCategoryGap: 0,
        itemStyle: { color: withAlpha(c.gridImport, 0.7) },
      },
      {
        name: l.gridExport,
        type: 'bar',
        xAxisIndex: 0,
        yAxisIndex: 0,
        data: points(
          start,
          data.exportW.map((w) => -w),
        ),
        barGap: '-100%',
        barCategoryGap: 0,
        itemStyle: { color: withAlpha(c.gridExport, 0.7) },
      },
      {
        name: l.soc,
        type: 'line',
        xAxisIndex: 1,
        yAxisIndex: 1,
        // The planned SoC is the value at the END of an interval.
        data: start.map((ms, i): Point => [ms + intervalMs(data, i), data.socPct[i] ?? null]),
        symbol: 'none',
        lineStyle: { color: c.battery, width: 2 },
        itemStyle: { color: c.battery },
        areaStyle: { color: withAlpha(c.battery, 0.12) },
        markLine: targetLines,
      },
      {
        name: l.importPrice,
        type: 'line',
        xAxisIndex: 2,
        yAxisIndex: 2,
        data: closed(data.importPrice),
        symbol: 'none',
        step: 'start',
        lineStyle: { color: c.gridImport, width: 2 },
        itemStyle: { color: c.gridImport },
      },
      {
        name: l.exportPrice,
        type: 'line',
        xAxisIndex: 2,
        yAxisIndex: 2,
        data: closed(data.exportPrice),
        symbol: 'none',
        step: 'start',
        lineStyle: { color: c.gridExport, width: 2, type: 'dashed' },
        itemStyle: { color: c.gridExport },
      },
    ],
  };
}

/** Length of interval `i` in ms (the next start, or the plan end for the last one). */
function intervalMs(data: ChartData, i: number): number {
  const next = i + 1 < data.start.length ? data.start[i + 1]! : data.endMs;
  return Math.max(0, next - data.start[i]!);
}

/** Height of the chart container in px for the layout above. */
export function chartHeight(compact: boolean): number {
  return (compact ? 92 : 56) + (compact ? 190 : 230) + 24 + 90 + 24 + 80 + 28;
}
