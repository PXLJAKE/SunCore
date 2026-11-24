<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  // Loaded on demand with the history view into the shared ECharts chunk (the plan chart's):
  // bar, line, Sankey and heatmap series on a canvas. The options come from the pure builders in
  // history/chartOptions.ts; this component only owns the chart instance, the theme colours, the
  // resize and the PNG export.
  import { BarChart, HeatmapChart, LineChart, SankeyChart } from 'echarts/charts';
  import {
    AxisPointerComponent,
    GridComponent,
    LegendComponent,
    TooltipComponent,
    VisualMapContinuousComponent,
  } from 'echarts/components';
  import { init, use, type ECharts, type EChartsCoreOption } from 'echarts/core';
  import { CanvasRenderer } from 'echarts/renderers';
  import { onMount } from 'svelte';
  import { t } from '../i18n/index.svelte';
  import type { HistoryColors } from '../history/chartOptions';

  use([
    BarChart,
    LineChart,
    SankeyChart,
    HeatmapChart,
    GridComponent,
    TooltipComponent,
    LegendComponent,
    VisualMapContinuousComponent,
    AxisPointerComponent,
    CanvasRenderer,
  ]);

  interface Props {
    /** Option of the chart for the theme colours and the layout; re-run on every change it reads. */
    build: (colors: HistoryColors, compact: boolean) => EChartsCoreOption;
    /** Accessible name; the data table sits next to the chart. */
    label: string;
    /** Name of the downloaded PNG, without extension. */
    filename: string;
    height?: number;
    describedby?: string;
    testid: string;
  }
  let { build, label, filename, height = 320, describedby, testid }: Props = $props();

  let el: HTMLDivElement | undefined = $state();
  let chart: ECharts | undefined;
  let width = $state(800);
  let themeTick = $state(0);

  const compact = $derived(width < 560);

  function readColors(node: HTMLElement): HistoryColors {
    const css = getComputedStyle(node);
    const v = (name: string, fallback: string): string =>
      css.getPropertyValue(name).trim() || fallback;
    return {
      text: v('--primary-text-color', '#212121'),
      textSecondary: v('--secondary-text-color', '#5f5f5f'),
      divider: v('--divider-color', '#c9ccd1'),
      card: v('--card-background-color', '#ffffff'),
      primary: v('--primary-color', '#0b6fa4'),
      pv: v('--energy-solar-color', '#c77700'),
      house: v('--primary-text-color', '#212121'),
      gridImport: v('--energy-grid-consumption-color', '#2f6fa8'),
      gridExport: v('--energy-grid-return-color', '#7040b8'),
      battery: v('--energy-battery-in-color', '#00796b'),
      batteryOut: v('--energy-battery-out-color', '#c2185b'),
      warning: v('--warning-color', '#8a5100'),
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

  $effect(() => {
    void themeTick;
    if (!chart || !el) return;
    chart.setOption(build(readColors(el), compact), { notMerge: true });
  });

  function download(): void {
    if (!chart || !el) return;
    const url = chart.getDataURL({
      type: 'png',
      pixelRatio: 2,
      backgroundColor: readColors(el).card,
    });
    const a = document.createElement('a');
    a.href = url;
    a.download = `${filename}.png`;
    a.click();
  }
</script>

<div
  bind:this={el}
  class="chart"
  style:height="{height}px"
  role="img"
  aria-label={label}
  aria-describedby={describedby}
  data-testid={testid}
></div>
<button type="button" class="btn secondary small png" data-testid="{testid}-png" onclick={download}>
  {t('history.export.png')}
</button>

<style>
  .chart {
    width: 100%;
    min-width: 0;
    touch-action: pan-y;
  }
  .png {
    justify-self: start;
  }
</style>
