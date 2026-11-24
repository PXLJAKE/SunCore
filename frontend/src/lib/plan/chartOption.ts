// SPDX-License-Identifier: Apache-2.0
// The ECharts option of the plan chart as a pure function of the plan data, the theme colours and
// the translated labels. No ECharts runtime is imported here (types only), so the logic is
// unit-testable without a canvas and stays out of the first-paint bundle.
import type { EChartsCoreOption } from 'echarts/core';
import type { ActualData } from './actual';
import type { CompareSeries } from './compare';
import { SEG_KINDS, type GanttLane, type SegKind } from './gantt';
import type { ChartData, TargetLines } from './model';
import { indexAt } from './model';
import type { TimeWindow } from './target';

/** Theme colours, read from the CSS variables (HA names) of the chart's container. */
export interface ChartColors {
  text: string;
  textSecondary: string;
  divider: string;
  card: string;
  pv: string;
  load: string;
  battery: string;
  devices: string;
  gridImport: string;
  gridExport: string;
  target: string;
  now: string;
  /** Discharge of the battery (Gantt). */
  batteryOut: string;
}

/** Everything the chart says, already translated and formatted for the active locale. */
export interface ChartLabels {
  pvBand: string;
  pvP50: string;
  load: string;
  devices: string;
  batteryPower: string;
  gridImport: string;
  gridExport: string;
  soc: string;
  importPrice: string;
  exportPrice: string;
  morningBuffer: (pct: number) => string;
  eveningTarget: (pct: number) => string;
  now: string;
  /** The marker of a decision (`#/plan?at=`). */
  decision: string;
  actualPv: string;
  actualHouse: string;
  actualGrid: string;
  actualBattery: string;
  actualSoc: string;
  comparedSoc: string;
  comparedBattery: string;
  /** Name of a Gantt lane (a device name, or the battery). */
  laneName: (id: string) => string;
  power: (w: number) => string;
  percent: (pct: number) => string;
  price: (value: number) => string;
  axisTime: (ms: number) => string;
  /** DOM content of the tooltip for the interval at `index`. */
  tooltip: (index: number, ms: number) => HTMLElement;
}

export interface ChartInput {
  data: ChartData;
  lines: TargetLines;
  nowMs: number;
  colors: ChartColors;
  labels: ChartLabels;
  /** Narrow screens need more room for the legend. */
  compact: boolean;
  /** The time axis; defaults to the span of the plan data. */
  window?: TimeWindow;
  /** What was measured; drawn as wide, faint lines under the planned ones. */
  actual?: ActualData | null;
  lanes?: readonly GanttLane[];
  /** Plan B of a comparison. */
  compare?: CompareSeries | null;
  /** The decision a link points at: its time and the interval it falls into. */
  marker?: { atMs: number; fromMs: number; toMs: number } | null;
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

/** Height of one Gantt lane: its name, the planned bar and the measured bar. */
export const LANE_HEIGHT = 48;
const GAP = 24;
const SOC_H = 90;
const PRICE_H = 80;

/** Rows the legend needs for `entries` names (the canvas wraps it by width). */
const legendRows = (entries: number, compact: boolean): number =>
  Math.ceil(entries / (compact ? 2 : 5));

interface Layout {
  gridTop: number;
  mainH: number;
  ganttH: number;
  total: number;
}

function layout(compact: boolean, entries: number, laneCount: number): Layout {
  const gridTop = 10 + legendRows(entries, compact) * 22 + 12;
  const mainH = compact ? 190 : 230;
  const ganttH = laneCount > 0 ? laneCount * LANE_HEIGHT + 6 : 0;
  const panels = mainH + GAP + SOC_H + GAP + PRICE_H + (ganttH > 0 ? GAP + ganttH : 0);
  return { gridTop, mainH, ganttH, total: gridTop + panels + 28 };
}

/** Height of the chart container in px for the layout above. */
export function chartHeight(compact: boolean, entries = 10, laneCount = 0): number {
  return layout(compact, entries, laneCount).total;
}

/** Number of legend names for the given extras (the container height depends on it). */
export const legendCount = (actual: boolean, compare: boolean): number =>
  10 + (actual ? 5 : 0) + (compare ? 2 : 0);

/** Fill colour of a segment kind (planned ones are drawn outlined and lighter). */
export function segColor(c: ChartColors, kind: SegKind): string {
  switch (kind) {
    case 'prefer_on':
    case 'run':
      return c.devices;
    case 'allowed':
      return withAlpha(c.devices, 0.5);
    case 'partial':
      return withAlpha(c.devices, 0.6);
    case 'prefer_off':
      return c.textSecondary;
    case 'charge':
      return c.battery;
    case 'discharge':
      return c.batteryOut;
    case 'lock':
      return c.target;
  }
}

interface RenderApi {
  value(dim: number): number;
  coord(value: [number, number]): [number, number];
  size(value: [number, number]): [number, number];
}
interface RenderParams {
  coordSys: { x: number; y: number; width: number; height: number };
}

/** A segment as a rectangle of the lane, cut at the plot area (zoom moves it past the edges). */
function renderSegment(c: ChartColors) {
  return (params: RenderParams, api: RenderApi) => {
    const cs = params.coordSys;
    const [x0, yCentre] = api.coord([api.value(0), api.value(2)]);
    const [x1] = api.coord([api.value(1), api.value(2)]);
    const band = api.size([0, 1])[1];
    const planned = api.value(3) === 0;
    const kind = SEG_KINDS[api.value(4)] ?? 'allowed';
    const barH = 12;
    const top = yCentre - band / 2 + 18 + (planned ? 0 : barH + 4);
    const left = Math.max(x0, cs.x);
    const right = Math.min(x1, cs.x + cs.width);
    if (right <= left) return { type: 'group', children: [] };
    const color = segColor(c, kind);
    return {
      type: 'rect',
      shape: { x: left, y: top, width: Math.max(1, right - left), height: barH, r: 2 },
      style: planned
        ? { fill: withAlpha(color, kind === 'allowed' ? 0.25 : 0.4), stroke: color, lineWidth: 1.5 }
        : { fill: color },
    };
  };
}

/** The name of a lane, above its bars. */
function renderLaneName(c: ChartColors, name: (lane: number) => string) {
  return (params: RenderParams, api: RenderApi) => {
    const cs = params.coordSys;
    const lane = api.value(2);
    const [, yCentre] = api.coord([api.value(0), lane]);
    const band = api.size([0, 1])[1];
    return {
      type: 'text',
      style: {
        text: name(lane),
        x: cs.x + 4,
        y: yCentre - band / 2 + 2,
        fill: c.text,
        fontSize: 12,
        fontWeight: 600,
      },
      silent: true,
    };
  };
}

export function buildOption(input: ChartInput): EChartsCoreOption {
  const { data, lines, nowMs, colors: c, labels: l, compact } = input;
  const start = data.start;
  const win = input.window ?? { fromMs: start[0] ?? 0, toMs: data.endMs };
  const actual = input.actual ?? null;
  const lanes = input.lanes ?? [];
  const compare = input.compare ?? null;
  const marker = input.marker ?? null;
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
  const actualClosed = (values: readonly (number | null)[]): Point[] => {
    if (!actual) return [];
    const pts = points(actual.times, values);
    const end = (actual.times.at(-1) ?? 0) + actual.stepMs;
    if (pts.length > 0) pts.push([end, null]);
    return pts;
  };
  const axisLabel = { color: c.textSecondary, fontSize: 11, hideOverlap: true };

  const legend = [
    l.pvBand,
    l.pvP50,
    l.load,
    l.devices,
    l.batteryPower,
    l.gridImport,
    l.gridExport,
    l.soc,
    l.importPrice,
    l.exportPrice,
    ...(actual ? [l.actualPv, l.actualHouse, l.actualGrid, l.actualBattery, l.actualSoc] : []),
    ...(compare ? [l.comparedSoc, l.comparedBattery] : []),
  ];
  const lay = layout(compact, legend.length, lanes.length);
  const right = compact ? 12 : 56;
  const socTop = lay.gridTop + lay.mainH + GAP;
  const priceTop = socTop + SOC_H + GAP;
  const ganttTop = priceTop + PRICE_H + GAP;
  const grids = [
    { left: 52, right, top: lay.gridTop, height: lay.mainH },
    { left: 52, right, top: socTop, height: SOC_H },
    { left: 52, right, top: priceTop, height: PRICE_H },
    ...(lanes.length > 0 ? [{ left: 52, right, top: ganttTop, height: lay.ganttH }] : []),
  ];
  const lastGrid = grids.length - 1;
  const xAxis = (gridIndex: number) => ({
    type: 'time' as const,
    gridIndex,
    min: win.fromMs,
    max: win.toMs,
    axisLine: { lineStyle: { color: c.divider } },
    splitLine: { show: true, lineStyle: { color: c.divider, opacity: 0.5 } },
    axisTick: { show: gridIndex === lastGrid },
    axisLabel: {
      ...axisLabel,
      show: gridIndex === lastGrid,
      formatter: (v: number) => l.axisTime(v),
    },
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
  const nowInside = nowMs >= win.fromMs && nowMs <= win.toMs;
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
    data: [
      ...(nowInside ? [{ xAxis: nowMs }] : []),
      ...(marker
        ? [
            {
              xAxis: marker.atMs,
              lineStyle: { color: c.target, type: 'dashed' as const, width: 2 },
              label: {
                show: true,
                formatter: l.decision,
                color: c.text,
                fontSize: 11,
                position: 'insideEndTop' as const,
              },
            },
          ]
        : []),
    ],
  };
  const markArea = marker
    ? {
        silent: true,
        itemStyle: { color: withAlpha(c.target, 0.15) },
        data: [[{ xAxis: marker.fromMs }, { xAxis: marker.toMs }]],
      }
    : undefined;
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
  /** What was measured: a wide, faint line behind the thin planned one of the same colour. */
  const actualLine = (
    name: string,
    gridIndex: number,
    values: readonly (number | null)[],
    color: string,
    stepped = true,
  ) => ({
    name,
    type: 'line',
    xAxisIndex: gridIndex,
    yAxisIndex: gridIndex,
    data: stepped
      ? actualClosed(values)
      : points(
          (actual?.times ?? []).map((ms) => ms + (actual?.stepMs ?? 0) / 2),
          values,
        ),
    symbol: 'none',
    step: stepped ? ('start' as const) : false,
    z: 1,
    lineStyle: { color, width: 5, opacity: 0.35 },
    itemStyle: { color },
  });
  const comparedLine = (
    name: string,
    gridIndex: number,
    data: Point[],
    color: string,
    stepped: boolean,
  ) => ({
    name,
    type: 'line',
    xAxisIndex: gridIndex,
    yAxisIndex: gridIndex,
    data,
    symbol: 'none',
    step: stepped ? ('start' as const) : false,
    z: 4,
    lineStyle: { color, width: 2, type: [6, 3] as number[] },
    itemStyle: { color },
  });

  const laneIndex = lanes.map((_, i) => i);
  const segData = lanes.flatMap((lane, i) =>
    [lane.planned, lane.actual].flatMap((segs, sub) =>
      segs.map((s) => [s.fromMs, s.toMs, i, sub, SEG_KINDS.indexOf(s.kind)]),
    ),
  );
  const ganttSeries =
    lanes.length > 0
      ? [
          {
            name: 'gantt',
            type: 'custom',
            xAxisIndex: 3,
            yAxisIndex: 3,
            data: segData,
            encode: { x: [0, 1], y: 2 },
            renderItem: renderSegment(c),
            z: 2,
          },
          {
            name: 'gantt-names',
            type: 'custom',
            xAxisIndex: 3,
            yAxisIndex: 3,
            data: laneIndex.map((i) => [win.fromMs, win.fromMs, i]),
            encode: { x: [0, 1], y: 2 },
            renderItem: renderLaneName(c, (i) => l.laneName(lanes[i]?.id ?? '')),
            silent: true,
            z: 3,
          },
        ]
      : [];

  return {
    animation: false,
    backgroundColor: 'transparent',
    textStyle: { color: c.text },
    legend: {
      top: 0,
      left: 0,
      itemWidth: 16,
      itemHeight: 8,
      itemGap: 10,
      textStyle: { color: c.text, fontSize: 12 },
      inactiveColor: c.textSecondary,
      data: legend,
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
        return l.tooltip(Math.max(0, indexAt(start, ms)), ms);
      },
    },
    axisPointer: { link: [{ xAxisIndex: 'all' }] },
    dataZoom: [
      {
        type: 'inside',
        xAxisIndex: grids.map((_, i) => i),
        filterMode: 'none',
        zoomOnMouseWheel: 'ctrl',
        moveOnMouseMove: true,
        moveOnMouseWheel: false,
        preventDefaultMouseMove: false,
        minSpan: 4,
      },
    ],
    grid: grids,
    xAxis: grids.map((_, i) => xAxis(i)),
    yAxis: [
      yAxis(0, (v) => l.power(v)),
      yAxis(1, (v) => l.percent(v), { min: 0, max: 100, interval: 25 }),
      yAxis(2, (v) => l.price(v), { scale: true }),
      ...(lanes.length > 0
        ? [
            {
              type: 'category' as const,
              gridIndex: 3,
              data: laneIndex,
              inverse: true,
              axisLine: { show: false },
              axisTick: { show: false },
              axisLabel: { show: false },
              splitLine: { show: true, lineStyle: { color: c.divider, opacity: 0.6 } },
            },
          ]
        : []),
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
        ...(markArea ? { markArea } : {}),
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
        name: l.devices,
        type: 'line',
        xAxisIndex: 0,
        yAxisIndex: 0,
        data: closed(data.devicesW),
        symbol: 'none',
        step: 'start',
        lineStyle: { color: c.devices, width: 2, type: 'dotted' },
        itemStyle: { color: c.devices },
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
      ...(actual
        ? [
            actualLine(l.actualPv, 0, actual.pvW, c.pv),
            actualLine(l.actualHouse, 0, actual.houseW, c.load),
            actualLine(l.actualGrid, 0, actual.gridW, c.gridImport),
            actualLine(l.actualBattery, 0, actual.batteryW, c.battery),
            actualLine(l.actualSoc, 1, actual.socPct, c.battery, false),
          ]
        : []),
      ...(compare
        ? [
            comparedLine(
              l.comparedSoc,
              1,
              start.map((ms, i): Point => [ms + intervalMs(data, i), compare.socPct[i] ?? null]),
              c.target,
              false,
            ),
            comparedLine(l.comparedBattery, 0, closed(compare.batteryW), c.target, true),
          ]
        : []),
      ...ganttSeries,
    ],
  };
}

/** Length of interval `i` in ms (the next start, or the plan end for the last one). */
function intervalMs(data: ChartData, i: number): number {
  const next = i + 1 < data.start.length ? data.start[i + 1]! : data.endMs;
  return Math.max(0, next - data.start[i]!);
}
