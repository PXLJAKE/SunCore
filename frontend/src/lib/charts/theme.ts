// SPDX-License-Identifier: Apache-2.0
// The ECharts theme as plain option defaults built from the design tokens (ADR-0019, 4B): no
// `registerTheme`, nothing from ECharts at runtime, so the chart chunks do not grow. The plan,
// history and what-if charts take their colours from here and merge the defaults with
// `withChartTheme`; a theme change (bridge or prefers-color-scheme) re-reads the tokens.
import type { ChartColors } from '../plan/chartOption';
import type { HistoryColors } from '../history/chartOptions';
import { readTokens, type ChartToken } from '../theme/tokens';

export interface ChartTheme {
  text: string;
  muted: string;
  /** Grid and axis lines (drawn at low opacity). */
  grid: string;
  surface: string;
  /** Tooltip surface. */
  raised: string;
  accent: string;
  warn: string;
  energy: {
    pv: string;
    gridIn: string;
    gridOut: string;
    batIn: string;
    batOut: string;
    house: string;
    device: string;
  };
  /** Axis label size in px (`--sc-font-xs`). */
  fontSize: number;
  /** Opacity of the grid lines. */
  gridOpacity: number;
  /** Opacity of uncertainty bands (P10-P90). */
  bandOpacity: number;
}

export function chartTheme(tok: Record<ChartToken, string>): ChartTheme {
  return {
    text: tok['--sc-text'],
    muted: tok['--sc-text-muted'],
    grid: tok['--sc-outline'],
    surface: tok['--sc-surface'],
    raised: tok['--sc-surface-raised'],
    accent: tok['--sc-accent'],
    warn: tok['--sc-warn'],
    energy: {
      pv: tok['--sc-energy-pv'],
      gridIn: tok['--sc-energy-grid-in'],
      gridOut: tok['--sc-energy-grid-out'],
      batIn: tok['--sc-energy-bat-in'],
      batOut: tok['--sc-energy-bat-out'],
      house: tok['--sc-energy-house'],
      device: tok['--sc-energy-device'],
    },
    fontSize: 12,
    gridOpacity: 0.7,
    bandOpacity: 0.18,
  };
}

/** The theme from the computed tokens of the chart's container. */
export const readChartTheme = (node: Element): ChartTheme => chartTheme(readTokens(node));

/** Colours of the plan chart and its Gantt. */
export function planColors(th: ChartTheme): ChartColors {
  return {
    text: th.text,
    textSecondary: th.muted,
    divider: th.grid,
    card: th.surface,
    pv: th.energy.pv,
    load: th.energy.house,
    devices: th.energy.device,
    battery: th.energy.batIn,
    batteryOut: th.energy.batOut,
    gridImport: th.energy.gridIn,
    gridExport: th.energy.gridOut,
    target: th.warn,
    now: th.muted,
  };
}

/** Colours of the history and what-if charts. */
export function historyColors(th: ChartTheme): HistoryColors {
  return {
    text: th.text,
    textSecondary: th.muted,
    divider: th.grid,
    card: th.surface,
    primary: th.accent,
    pv: th.energy.pv,
    house: th.energy.house,
    gridImport: th.energy.gridIn,
    gridExport: th.energy.gridOut,
    battery: th.energy.batIn,
    batteryOut: th.energy.batOut,
    warning: th.warn,
  };
}

type Obj = Record<string, unknown>;
const isObj = (v: unknown): v is Obj => typeof v === 'object' && v !== null && !Array.isArray(v);

/** `base` under `over`: values of `over` win, nested plain objects are merged. */
function under(base: Obj, over: unknown): Obj {
  if (!isObj(over)) return { ...base };
  const out: Obj = { ...base };
  for (const [k, v] of Object.entries(over)) {
    out[k] = isObj(v) && isObj(base[k]) ? under(base[k] as Obj, v) : v;
  }
  return out;
}

const eachOf = (v: unknown, f: (o: Obj) => Obj): unknown =>
  Array.isArray(v) ? v.map((x) => (isObj(x) ? f(x) : x)) : isObj(v) ? f(v) : v;

/** Option defaults of the chart theme (text, tooltip surface, legend, axes). */
export function chartDefaults(th: ChartTheme) {
  return {
    textStyle: { color: th.text, fontSize: th.fontSize },
    tooltip: {
      backgroundColor: th.raised,
      borderColor: th.grid,
      borderWidth: 1,
      padding: [8, 12],
      textStyle: { color: th.text, fontSize: th.fontSize },
      // The tooltip is a DOM node in the page, so the CSS tokens apply directly.
      extraCssText: 'border-radius: var(--sc-radius-md); box-shadow: var(--sc-elev-2);',
    },
    legend: {
      textStyle: { color: th.text, fontSize: th.fontSize },
      inactiveColor: th.muted,
      icon: 'roundRect',
    },
    axis: {
      axisLine: { lineStyle: { color: th.grid } },
      axisTick: { show: false },
      axisLabel: { color: th.muted, fontSize: th.fontSize, hideOverlap: true },
      splitLine: { lineStyle: { color: th.grid, opacity: th.gridOpacity } },
    },
  };
}

/**
 * The option with the theme defaults merged underneath: what a builder sets wins, the theme fills
 * the rest. Tooltip CSS is appended to the builder's own `extraCssText`.
 */
export function withChartTheme<O extends object>(option: O, th: ChartTheme): O {
  const d = chartDefaults(th);
  const o = option as Obj;
  const out: Obj = { ...o, textStyle: under(d.textStyle, o['textStyle']) };
  if (o['tooltip'] !== undefined) {
    out['tooltip'] = eachOf(o['tooltip'], (tt) => {
      const merged = under(d.tooltip, tt);
      const own = typeof tt['extraCssText'] === 'string' ? `${tt['extraCssText']} ` : '';
      merged['extraCssText'] = `${own}${d.tooltip.extraCssText}`;
      return merged;
    });
  }
  if (o['legend'] !== undefined) out['legend'] = eachOf(o['legend'], (l) => under(d.legend, l));
  for (const axis of ['xAxis', 'yAxis'] as const) {
    if (o[axis] !== undefined) out[axis] = eachOf(o[axis], (a) => under(d.axis, a));
  }
  return out as O;
}
