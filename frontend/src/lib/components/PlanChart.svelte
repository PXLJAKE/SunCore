<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  // The only module that imports ECharts: loaded on demand with the plan view (own chunk). Only
  // line and bar series and the grid, tooltip, legend, markLine and inside-zoom components are
  // registered, rendered on a canvas.
  import { BarChart, LineChart } from 'echarts/charts';
  import {
    AxisPointerComponent,
    DataZoomInsideComponent,
    GridComponent,
    LegendComponent,
    MarkLineComponent,
    TooltipComponent,
  } from 'echarts/components';
  import { init, use, type ECharts } from 'echarts/core';
  import { CanvasRenderer } from 'echarts/renderers';
  import { onMount } from 'svelte';
  import { i18n, t, tDynamic, tNumber, tPercent, tPower, tTime } from '../i18n/index.svelte';
  import {
    buildOption,
    chartHeight,
    type ChartColors,
    type ChartLabels,
  } from '../plan/chartOption';
  import { indexAt, type ChartData, type TargetLines } from '../plan/model';

  use([
    LineChart,
    BarChart,
    GridComponent,
    TooltipComponent,
    LegendComponent,
    MarkLineComponent,
    DataZoomInsideComponent,
    AxisPointerComponent,
    CanvasRenderer,
  ]);

  interface Props {
    data: ChartData;
    lines: TargetLines;
    nowMs: number;
    currency: string;
    /** Accessible name of the chart; the text alternative sits next to it. */
    label: string;
    describedby?: string;
  }
  let { data, lines, nowMs, currency, label, describedby }: Props = $props();

  let el: HTMLDivElement | undefined = $state();
  let chart: ECharts | undefined;
  let width = $state(800);
  /** Bumped when the theme changes so the colours are read again. */
  let themeTick = $state(0);

  const compact = $derived(width < 560);
  const height = $derived(chartHeight(compact));

  function readColors(node: HTMLElement): ChartColors {
    const css = getComputedStyle(node);
    const v = (name: string, fallback: string): string =>
      css.getPropertyValue(name).trim() || fallback;
    return {
      text: v('--primary-text-color', '#212121'),
      textSecondary: v('--secondary-text-color', '#5f5f5f'),
      divider: v('--divider-color', '#c9ccd1'),
      card: v('--card-background-color', '#ffffff'),
      pv: v('--energy-solar-color', '#c77700'),
      load: v('--primary-text-color', '#212121'),
      battery: v('--energy-battery-in-color', '#00796b'),
      gridImport: v('--energy-grid-consumption-color', '#2f6fa8'),
      gridExport: v('--energy-grid-return-color', '#7040b8'),
      target: v('--warning-color', '#8a5100'),
      now: v('--secondary-text-color', '#5f5f5f'),
    };
  }

  const dayFormat = (ms: number): string =>
    new Intl.DateTimeFormat(i18n.locale, { weekday: 'short', day: 'numeric' }).format(new Date(ms));

  function tooltipFor(index: number): HTMLElement {
    const root = document.createElement('div');
    const add = (text: string, strong = false): HTMLElement => {
      const row = document.createElement(strong ? 'strong' : 'div');
      row.textContent = text;
      root.append(row);
      return row;
    };
    const start = data.start[index];
    if (start === undefined) return root;
    const end = index + 1 < data.start.length ? data.start[index + 1]! : data.endMs;
    add(t('plan.tt.when', { from: tTime(new Date(start)), to: tTime(new Date(end)) }), true);
    add(
      t('plan.tt.pv', {
        p50: tPower(data.pvP50[index] ?? 0),
        p10: tPower(data.pvP10[index] ?? data.pvP50[index] ?? 0),
        p90: tPower(data.pvP90[index] ?? data.pvP50[index] ?? 0),
      }),
    );
    add(t('plan.tt.load', { power: tPower(data.loadW[index] ?? 0) }));
    add(t('plan.tt.battery', { power: tPower(data.batteryW[index] ?? 0) }));
    add(
      t('plan.tt.grid', {
        imported: tPower(data.importW[index] ?? 0),
        exported: tPower(data.exportW[index] ?? 0),
      }),
    );
    const soc = data.socPct[index];
    if (soc != null) add(t('plan.tt.soc', { soc: tPercent(soc) }));
    const ip = data.importPrice[index];
    const ep = data.exportPrice[index];
    if (ip != null && ep != null) {
      add(t('plan.tt.price', { imported: price(ip), exported: price(ep), currency }));
    }
    const reasons = data.reasons[index] ?? [];
    if (reasons.length > 0) {
      add(t('plan.tt.why'), true);
      const ul = document.createElement('ul');
      ul.style.margin = '2px 0 0';
      ul.style.paddingLeft = '16px';
      for (const r of reasons) {
        const li = document.createElement('li');
        li.textContent = tDynamic(r.key, r.args);
        ul.append(li);
      }
      root.append(ul);
    }
    return root;
  }

  const price = (v: number): string =>
    tNumber(v, { minimumFractionDigits: 2, maximumFractionDigits: 3 });

  function labels(): ChartLabels {
    return {
      pvBand: t('plan.s.pv_band'),
      pvP50: t('plan.s.pv_p50'),
      load: t('plan.s.load'),
      batteryPower: t('plan.s.battery_power'),
      gridImport: t('plan.s.grid_import'),
      gridExport: t('plan.s.grid_export'),
      soc: t('plan.s.soc'),
      importPrice: t('plan.s.import_price'),
      exportPrice: t('plan.s.export_price'),
      morningBuffer: (pct) => t('plan.line.morning', { soc: tPercent(pct) }),
      eveningTarget: (pct) => t('plan.line.evening', { soc: tPercent(pct) }),
      now: t('plan.now'),
      power: (w) => tPower(w),
      percent: (pct) => tPercent(pct),
      price,
      axisTime: (ms) => (new Date(ms).getHours() === 0 ? dayFormat(ms) : tTime(new Date(ms))),
      tooltip: tooltipFor,
    };
  }

  onMount(() => {
    if (!el) return;
    chart = init(el, undefined, { renderer: 'canvas' });
    const resize = new ResizeObserver(() => {
      if (!el) return;
      width = el.clientWidth;
      chart?.resize();
    });
    resize.observe(el);
    // Colours follow the theme: the system scheme and the `data-theme` override of the root.
    const media = window.matchMedia('(prefers-color-scheme: dark)');
    const bump = (): void => void (themeTick += 1);
    media.addEventListener('change', bump);
    const watch = new MutationObserver(bump);
    watch.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ['data-theme', 'style'],
    });
    return () => {
      resize.disconnect();
      watch.disconnect();
      media.removeEventListener('change', bump);
      chart?.dispose();
      chart = undefined;
    };
  });

  // One setOption per change of data, locale, theme or width: ECharts diffs the series.
  $effect(() => {
    void themeTick;
    if (!chart || !el) return;
    chart.setOption(
      buildOption({
        data,
        lines,
        nowMs,
        colors: readColors(el),
        labels: labels(),
        compact,
      }),
      { notMerge: true },
    );
  });

  // The tooltip needs an index into the data: keep it reachable for tests and the text view.
  export function reasonsAt(ms: number): number {
    return indexAt(data.start, ms);
  }
</script>

<div
  bind:this={el}
  class="chart"
  style:height="{height}px"
  role="img"
  aria-label={label}
  aria-describedby={describedby}
  data-testid="plan-chart"
></div>

<style>
  .chart {
    width: 100%;
    min-width: 0;
    touch-action: pan-y;
  }
</style>
