// SPDX-License-Identifier: Apache-2.0
// ECharts options of the history charts as pure functions of the API data, the theme colours and
// the translated, formatted labels. Only types are imported from ECharts, so the logic is
// testable without a canvas and stays out of the first-paint bundle (the runtime is registered
// in HistoryChart.svelte, in the chart chunk).
import type { EChartsCoreOption } from 'echarts/core';
import type {
  HistoryBucket,
  HistoryCycles,
  HistoryHeatmap,
  HistorySankey,
  HistorySkill,
} from '../api/types';
import { withAlpha } from '../plan/chartOption';

export interface HistoryColors {
  text: string;
  textSecondary: string;
  divider: string;
  card: string;
  primary: string;
  pv: string;
  house: string;
  gridImport: string;
  gridExport: string;
  battery: string;
  batteryOut: string;
  warning: string;
}

/** Everything the charts say, already translated and formatted for the active locale. */
export interface HistoryText {
  series: {
    pv: string;
    house: string;
    import: string;
    export: string;
    costNet: string;
    baselineNoPv: string;
    baselinePvOnly: string;
    cycles: string;
    socMin: string;
    socMax: string;
  };
  node: Record<HistorySankey['nodes'][number], string>;
  incomplete: string;
  energy: (wh: number) => string;
  money: (v: number) => string;
  power: (w: number) => string;
  percent: (pct: number) => string;
  number: (v: number) => string;
  /** Axis label of a bucket key (`2026-06-15` or `2026-06`). */
  bucket: (key: string) => string;
  /** Axis label of a day (`2026-06-15`). */
  day: (day: string) => string;
  weekday: (index: number) => string;
  hour: (hour: number) => string;
  /** Series name of a forecast-skill series. */
  skillName: (source: string, kind: string, horizon: string) => string;
}

export interface Ctx {
  colors: HistoryColors;
  text: HistoryText;
  compact: boolean;
}

type Cell = number | null;

type Look = Pick<Ctx, 'colors' | 'compact'>;

export const base = (c: Look): Record<string, unknown> => ({
  animation: false,
  textStyle: { color: c.colors.text },
  grid: { left: 8, right: 12, top: c.compact ? 64 : 40, bottom: 8, containLabel: true },
  legend: {
    top: 0,
    type: 'scroll',
    textStyle: { color: c.colors.text },
    pageTextStyle: { color: c.colors.textSecondary },
  },
});

export const axisStyle = (c: Pick<Ctx, 'colors'>) => ({
  axisLine: { lineStyle: { color: c.colors.divider } },
  axisLabel: { color: c.colors.textSecondary },
  splitLine: { lineStyle: { color: c.colors.divider, opacity: 0.6 } },
});

/** A tooltip of text rows built with `textContent` (no HTML from data). */
export function rowsTooltip(rows: (string | { strong: string })[]): HTMLElement {
  const root = document.createElement('div');
  for (const row of rows) {
    const el = document.createElement(typeof row === 'string' ? 'div' : 'strong');
    el.textContent = typeof row === 'string' ? row : row.strong;
    root.append(el);
  }
  return root;
}

interface AxisParam {
  axisValue: string;
  dataIndex: number;
  seriesName: string;
  value: Cell | unknown;
  marker?: string;
}

export function axisTooltip(
  c: unknown,
  fmt: (v: number) => string,
  heading: (axis: string, index: number) => string,
  note?: (index: number) => string | null,
): (p: unknown) => HTMLElement {
  return (p) => {
    const params = (Array.isArray(p) ? p : [p]) as AxisParam[];
    const first = params[0];
    if (!first) return rowsTooltip([]);
    const rows: (string | { strong: string })[] = [
      { strong: heading(first.axisValue, first.dataIndex) },
    ];
    for (const x of params) {
      const v = typeof x.value === 'number' ? fmt(x.value) : '–';
      rows.push(`${x.seriesName}: ${v}`);
    }
    const extra = note?.(first.dataIndex);
    if (extra) rows.push(extra);
    void c;
    return rowsTooltip(rows);
  };
}

function bars(
  name: string,
  color: string,
  values: readonly Cell[],
  incomplete: readonly boolean[],
  stack?: string,
): Record<string, unknown> {
  return {
    name,
    type: 'bar',
    stack,
    itemStyle: { color },
    barMaxWidth: 28,
    data: values.map((v, i) =>
      incomplete[i]
        ? {
            value: v,
            itemStyle: {
              color: withAlpha(color, 0.45),
              borderColor: color,
              borderType: 'dashed',
              borderWidth: 1,
            },
          }
        : v,
    ),
  };
}

const kwh = (wh: number): number => Math.round(wh) / 1000;

/** Energy per bucket: PV, consumption, import and export (kWh). */
export function energyOption(buckets: readonly HistoryBucket[], c: Ctx): EChartsCoreOption {
  const inc = buckets.map((b) => !b.complete);
  const col = (pick: (b: HistoryBucket) => number): Cell[] => buckets.map((b) => kwh(pick(b)));
  const { series: s } = c.text;
  return {
    ...base(c),
    tooltip: {
      trigger: 'axis',
      appendToBody: false,
      formatter: axisTooltip(
        c,
        (v) => c.text.energy(v * 1000),
        (axis) => c.text.bucket(axis),
        (i) => (inc[i] ? c.text.incomplete : null),
      ),
    },
    xAxis: {
      type: 'category',
      data: buckets.map((b) => b.key),
      axisLabel: { color: c.colors.textSecondary, formatter: (v: string) => c.text.bucket(v) },
    },
    yAxis: {
      type: 'value',
      ...axisStyle(c),
      axisLabel: {
        color: c.colors.textSecondary,
        formatter: (v: number) => c.text.energy(v * 1000),
      },
    },
    series: [
      bars(
        s.pv,
        c.colors.pv,
        col((b) => b.energy.pv_wh),
        inc,
      ),
      bars(
        s.house,
        c.colors.house,
        col((b) => b.energy.house_wh),
        inc,
      ),
      bars(
        s.import,
        c.colors.gridImport,
        col((b) => b.energy.import_wh),
        inc,
      ),
      bars(
        s.export,
        c.colors.gridExport,
        col((b) => b.energy.export_wh),
        inc,
      ),
    ],
  };
}

/** Cost per bucket against the two baselines (currency; `null` = no stored prices). */
export function costOption(buckets: readonly HistoryBucket[], c: Ctx): EChartsCoreOption {
  const inc = buckets.map((b) => !b.complete);
  const { series: s } = c.text;
  const round = (v: number | null): Cell => (v == null ? null : Math.round(v * 100) / 100);
  return {
    ...base(c),
    tooltip: {
      trigger: 'axis',
      formatter: axisTooltip(
        c,
        c.text.money,
        (axis) => c.text.bucket(axis),
        (i) => (inc[i] ? c.text.incomplete : null),
      ),
    },
    xAxis: {
      type: 'category',
      data: buckets.map((b) => b.key),
      axisLabel: { color: c.colors.textSecondary, formatter: (v: string) => c.text.bucket(v) },
    },
    yAxis: {
      type: 'value',
      ...axisStyle(c),
      axisLabel: { color: c.colors.textSecondary, formatter: (v: number) => c.text.money(v) },
    },
    series: [
      bars(
        s.costNet,
        c.colors.primary,
        buckets.map((b) => round(b.cost.cost_net)),
        inc,
      ),
      bars(
        s.baselinePvOnly,
        c.colors.warning,
        buckets.map((b) => round(b.cost.baseline_pv_only_cost)),
        inc,
      ),
      bars(
        s.baselineNoPv,
        c.colors.textSecondary,
        buckets.map((b) => round(b.cost.baseline_no_pv_cost)),
        inc,
      ),
    ],
  };
}

/** The six flows of the period as a Sankey diagram (kWh; zero flows are left out). */
export function sankeyOption(data: HistorySankey, c: Ctx): EChartsCoreOption {
  const colorOf: Record<string, string> = {
    pv: c.colors.pv,
    grid_import: c.colors.gridImport,
    battery_discharge: c.colors.batteryOut,
    house: c.colors.house,
    battery_charge: c.colors.battery,
    grid_export: c.colors.gridExport,
  };
  const links = data.links.filter((l) => l.wh > 0);
  const used = new Set(links.flatMap((l) => [l.source, l.target]));
  return {
    animation: false,
    textStyle: { color: c.colors.text },
    tooltip: {
      trigger: 'item',
      formatter: (p: {
        dataType?: string;
        data: { source?: string; target?: string; name?: string; value?: number };
      }) => {
        const d = p.data;
        if (p.dataType === 'edge' && d.source && d.target) {
          return rowsTooltip([
            {
              strong: `${c.text.node[d.source as keyof typeof c.text.node]} → ${c.text.node[d.target as keyof typeof c.text.node]}`,
            },
            c.text.energy((d.value ?? 0) * 1000),
          ]);
        }
        return rowsTooltip([
          { strong: c.text.node[(d.name ?? '') as keyof typeof c.text.node] ?? '' },
          c.text.energy((p as unknown as { value: number }).value * 1000),
        ]);
      },
    },
    series: [
      {
        type: 'sankey',
        left: 8,
        right: c.compact ? 90 : 130,
        top: 8,
        bottom: 8,
        nodeWidth: 14,
        nodeGap: 14,
        draggable: false,
        emphasis: { focus: 'adjacency' },
        lineStyle: { color: 'gradient', opacity: 0.45, curveness: 0.5 },
        label: {
          color: c.colors.text,
          formatter: (p: { name: string }) => c.text.node[p.name as keyof typeof c.text.node],
        },
        data: data.nodes
          .filter((n) => used.has(n))
          .map((n) => ({ name: n, itemStyle: { color: colorOf[n], borderColor: c.colors.card } })),
        links: links.map((l) => ({ source: l.source, target: l.target, value: kwh(l.wh) })),
      },
    ],
  };
}

/** Mean by weekday (rows, Monday first) and hour of day (columns). */
export function heatmapOption(data: HistoryHeatmap, c: Ctx): EChartsCoreOption {
  const cells: [number, number, number | null][] = [];
  let max = 0;
  data.values.forEach((row, d) =>
    row.forEach((v, h) => {
      cells.push([h, d, v]);
      if (v != null && v > max) max = v;
    }),
  );
  return {
    animation: false,
    textStyle: { color: c.colors.text },
    tooltip: {
      formatter: (p: { value: [number, number, number | null] }) => {
        const [h, d, v] = p.value;
        return rowsTooltip([
          { strong: `${c.text.weekday(d)}, ${c.text.hour(h)}` },
          v == null ? '–' : c.text.power(v),
        ]);
      },
    },
    grid: { left: 8, right: 12, top: 8, bottom: 56, containLabel: true },
    xAxis: {
      type: 'category',
      data: Array.from({ length: 24 }, (_, h) => h),
      splitArea: { show: true },
      axisLabel: {
        color: c.colors.textSecondary,
        interval: c.compact ? 5 : 2,
        formatter: (v: string) => c.text.hour(Number(v)),
      },
    },
    yAxis: {
      type: 'category',
      inverse: true,
      data: Array.from({ length: 7 }, (_, i) => c.text.weekday(i)),
      axisLabel: { color: c.colors.textSecondary },
    },
    visualMap: {
      min: 0,
      max: Math.max(max, 1),
      calculable: false,
      orient: 'horizontal',
      left: 'center',
      bottom: 4,
      itemWidth: 12,
      itemHeight: c.compact ? 140 : 220,
      textStyle: { color: c.colors.textSecondary },
      formatter: (v: number) => c.text.power(v),
      inRange: { color: [withAlpha(c.colors.primary, 0.1), c.colors.primary] },
    },
    series: [
      {
        type: 'heatmap',
        data: cells,
        itemStyle: { borderColor: c.colors.card, borderWidth: 1 },
        emphasis: { itemStyle: { borderColor: c.colors.text } },
      },
    ],
  };
}

/** Equivalent full cycles per day (bars) and the state-of-charge range (lines). */
export function cyclesOption(data: HistoryCycles, c: Ctx): EChartsCoreOption {
  const days = data.days;
  const inc = days.map((d) => !d.complete);
  const { series: s } = c.text;
  const pct = (v: number | null): Cell => (v == null ? null : v);
  return {
    ...base(c),
    tooltip: {
      trigger: 'axis',
      formatter: (p: unknown) => {
        const params = (Array.isArray(p) ? p : [p]) as AxisParam[];
        const first = params[0];
        if (!first) return rowsTooltip([]);
        const rows: (string | { strong: string })[] = [{ strong: c.text.day(first.axisValue) }];
        for (const x of params) {
          const isPct = x.seriesName !== s.cycles;
          const v =
            typeof x.value === 'number'
              ? isPct
                ? c.text.percent(x.value)
                : c.text.number(x.value)
              : '–';
          rows.push(`${x.seriesName}: ${v}`);
        }
        if (inc[first.dataIndex]) rows.push(c.text.incomplete);
        return rowsTooltip(rows);
      },
    },
    xAxis: {
      type: 'category',
      data: days.map((d) => d.day),
      axisLabel: { color: c.colors.textSecondary, formatter: (v: string) => c.text.day(v) },
    },
    yAxis: [
      {
        type: 'value',
        ...axisStyle(c),
        min: 0,
        axisLabel: { color: c.colors.textSecondary, formatter: (v: number) => c.text.number(v) },
      },
      {
        type: 'value',
        min: 0,
        max: 100,
        splitLine: { show: false },
        axisLabel: { color: c.colors.textSecondary, formatter: (v: number) => c.text.percent(v) },
      },
    ],
    series: [
      bars(
        s.cycles,
        c.colors.battery,
        days.map((d) => d.equivalent_full_cycles),
        inc,
      ),
      {
        name: s.socMax,
        type: 'line',
        yAxisIndex: 1,
        showSymbol: false,
        itemStyle: { color: c.colors.warning },
        lineStyle: { width: 2 },
        data: days.map((d) => pct(d.soc_max_pct)),
      },
      {
        name: s.socMin,
        type: 'line',
        yAxisIndex: 1,
        showSymbol: false,
        itemStyle: { color: c.colors.batteryOut },
        lineStyle: { width: 2, type: 'dashed' },
        data: days.map((d) => pct(d.soc_min_pct)),
      },
    ],
  };
}

const SKILL_COLORS = [
  'pv',
  'gridImport',
  'battery',
  'gridExport',
  'batteryOut',
  'warning',
] as const;

/** Mean absolute error per day, one line per source, kind and horizon (W). */
export function skillOption(data: HistorySkill, c: Ctx): EChartsCoreOption {
  const days = [...new Set(data.series.flatMap((s) => s.points.map((p) => p.day)))].sort();
  return {
    ...base(c),
    tooltip: {
      trigger: 'axis',
      formatter: axisTooltip(c, c.text.power, (axis) => c.text.day(axis)),
    },
    xAxis: {
      type: 'category',
      data: days,
      axisLabel: { color: c.colors.textSecondary, formatter: (v: string) => c.text.day(v) },
    },
    yAxis: {
      type: 'value',
      min: 0,
      ...axisStyle(c),
      axisLabel: { color: c.colors.textSecondary, formatter: (v: number) => c.text.power(v) },
    },
    series: data.series.map((s, i) => {
      const byDay = new Map(s.points.map((p) => [p.day, p.mae_w]));
      const color = c.colors[SKILL_COLORS[i % SKILL_COLORS.length]!];
      return {
        name: c.text.skillName(s.source_id, s.kind, s.horizon),
        type: 'line',
        showSymbol: false,
        connectNulls: false,
        itemStyle: { color },
        lineStyle: { width: 2, type: i < SKILL_COLORS.length ? 'solid' : 'dashed' },
        data: days.map((d) => byDay.get(d) ?? null),
      };
    }),
  };
}
