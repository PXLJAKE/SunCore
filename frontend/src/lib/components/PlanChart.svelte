<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  // The only module that imports the plan's ECharts parts: loaded on demand with the plan view.
  // The shared `echarts-core` chunk carries line, bar, grid, tooltip, legend, markLine and the
  // canvas renderer; the Gantt (`custom`) and the zoom (`dataZoom`) live in `echarts-plan`.
  import { BarChart, CustomChart, LineChart } from 'echarts/charts';
  import {
    AxisPointerComponent,
    DataZoomInsideComponent,
    GridComponent,
    LegendComponent,
    MarkAreaComponent,
    MarkLineComponent,
    TooltipComponent,
  } from 'echarts/components';
  import { init, use, type ECharts } from 'echarts/core';
  import { CanvasRenderer } from 'echarts/renderers';
  import { mount, onMount, unmount, untrack } from 'svelte';
  import {
    i18n,
    t,
    tClock,
    tDateTime,
    tNumber,
    tPercent,
    tPower,
    tTime,
    type MessageKey,
  } from '../i18n/index.svelte';
  import { actualIndex, type ActualData } from '../plan/actual';
  import {
    buildOption,
    chartHeight,
    legendCount,
    segColor,
    type ChartColors,
    type ChartLabels,
  } from '../plan/chartOption';
  import type { CompareSeries } from '../plan/compare';
  import { BATTERY_LANE, segAt, deviceShareAt, type GanttLane, type SegKind } from '../plan/gantt';
  import { indexAt, type ChartData, type TargetLines } from '../plan/model';
  import type { TimeWindow } from '../plan/target';
  import { FULL, applyZoom, isZoomed, visibleSpan, zoomKey, type ZoomAction } from '../plan/zoom';
  import ReasonList from './ReasonList.svelte';

  use([
    LineChart,
    BarChart,
    CustomChart,
    GridComponent,
    TooltipComponent,
    LegendComponent,
    MarkLineComponent,
    MarkAreaComponent,
    DataZoomInsideComponent,
    AxisPointerComponent,
    CanvasRenderer,
  ]);

  const SEG_LABEL: Record<SegKind, MessageKey> = {
    prefer_on: 'plan.seg.prefer_on',
    allowed: 'plan.seg.allowed',
    prefer_off: 'plan.seg.prefer_off',
    charge: 'plan.seg.charge',
    discharge: 'plan.seg.discharge',
    lock: 'plan.seg.lock',
    run: 'plan.seg.run',
    partial: 'plan.seg.partial',
    start: 'plan.seg.start',
  };

  interface Props {
    data: ChartData;
    window: TimeWindow;
    lines: TargetLines;
    nowMs: number;
    currency: string;
    /** Accessible name of the chart; the text alternative sits next to it. */
    label: string;
    describedby?: string;
    actual?: ActualData | null;
    lanes?: readonly GanttLane[];
    compare?: CompareSeries | null;
    /** The decision a link points at. */
    marker?: { atMs: number; fromMs: number; toMs: number } | null;
    deviceName: (id: string) => string;
    /** Name of the PNG, without extension. */
    filename: string;
  }
  let {
    data,
    window: win,
    lines,
    nowMs,
    currency,
    label,
    describedby,
    actual = null,
    lanes = [],
    compare = null,
    marker = null,
    deviceName,
    filename,
  }: Props = $props();

  let el: HTMLDivElement | undefined = $state();
  let chart: ECharts | undefined;
  let width = $state(800);
  /** Bumped when the theme changes so the colours are read again. */
  let themeTick = $state(0);
  let zoom = $state({ ...FULL });
  let colors = $state<ChartColors | null>(null);
  let tip: ReturnType<typeof mount> | null = null;

  const compact = $derived(width < 560);
  const height = $derived(
    chartHeight(compact, legendCount(actual !== null, compare !== null), lanes.length),
  );
  const zoomed = $derived(isZoomed(zoom));
  const visible = $derived(visibleSpan(win.fromMs, win.toMs, zoom));

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
      devices: v('--info-color', '#0b6fa4'),
      battery: v('--energy-battery-in-color', '#00796b'),
      batteryOut: v('--energy-battery-out-color', '#c2185b'),
      gridImport: v('--energy-grid-consumption-color', '#2f6fa8'),
      gridExport: v('--energy-grid-return-color', '#7040b8'),
      target: v('--warning-color', '#8a5100'),
      now: v('--secondary-text-color', '#5f5f5f'),
    };
  }

  const dayFormat = (ms: number): string =>
    new Intl.DateTimeFormat(i18n.locale, {
      weekday: 'short',
      day: 'numeric',
      timeZone: i18n.timeZone,
    }).format(new Date(ms));

  const price = (v: number): string =>
    tNumber(v, { minimumFractionDigits: 2, maximumFractionDigits: 3 });
  const orDash = (v: number | null | undefined, f: (n: number) => string): string =>
    v == null ? '–' : f(v);
  const laneName = (id: string): string =>
    id === BATTERY_LANE ? t('plan.lane.battery') : deviceName(id);

  function tooltipFor(index: number, ms: number): HTMLElement {
    if (tip) {
      void unmount(tip);
      tip = null;
    }
    const root = document.createElement('div');
    const add = (text: string, strong = false): void => {
      const row = document.createElement(strong ? 'strong' : 'div');
      row.textContent = text;
      if (strong) row.style.display = 'block';
      root.append(row);
    };
    const first = data.start[index];
    if (first === undefined) return root;
    const end = index + 1 < data.start.length ? data.start[index + 1]! : data.endMs;
    add(t('plan.tt.when', { from: tTime(new Date(first)), to: tTime(new Date(end)) }), true);
    if (ms >= first && ms < end) {
      add(
        t('plan.tt.pv', {
          p50: tPower(data.pvP50[index] ?? 0),
          p10: tPower(data.pvP10[index] ?? data.pvP50[index] ?? 0),
          p90: tPower(data.pvP90[index] ?? data.pvP50[index] ?? 0),
        }),
      );
      add(t('plan.tt.load', { power: tPower(data.loadW[index] ?? 0) }));
      add(t('plan.tt.devices', { power: tPower(data.devicesW[index] ?? 0) }));
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
    } else {
      add(t('plan.tt.no_plan'));
    }
    if (compare) {
      add(
        t('plan.tt.compared', {
          soc: orDash(compare.socPct[index], tPercent),
          power: tPower(compare.batteryW[index] ?? 0),
        }),
      );
    }
    if (actual) {
      const i = actualIndex(actual, ms);
      if (i >= 0) {
        add(t('plan.tt.actual'), true);
        add(
          t('plan.tt.actual_flows', {
            pv: orDash(actual.pvW[i], tPower),
            house: orDash(actual.houseW[i], tPower),
            grid: orDash(actual.gridW[i], tPower),
            battery: orDash(actual.batteryW[i], tPower),
            soc: orDash(actual.socPct[i], tPercent),
          }),
        );
      }
    }
    for (const lane of lanes) {
      const planned = segAt(lane.planned, ms);
      const measured = segAt(lane.actual, ms);
      const share = lane.battery ? null : deviceShareAt(actual, lane.id, ms);
      add(
        t('plan.tt.lane', {
          name: laneName(lane.id),
          planned: planned ? t(SEG_LABEL[planned.kind]) : t('plan.seg.none'),
          measured: measured
            ? t(SEG_LABEL[measured.kind])
            : share == null && lane.actual.length === 0
              ? '–'
              : t('plan.seg.none'),
        }),
      );
    }
    const reasons = data.reasons[index] ?? [];
    if (reasons.length > 0) {
      add(t('plan.tt.why'), true);
      const holder = document.createElement('div');
      root.append(holder);
      tip = mount(ReasonList, { target: holder, props: { reasons } });
    }
    return root;
  }

  function labels(): ChartLabels {
    return {
      pvBand: t('plan.s.pv_band'),
      pvP50: t('plan.s.pv_p50'),
      load: t('plan.s.load_base'),
      devices: t('plan.s.devices'),
      batteryPower: t('plan.s.battery_power'),
      gridImport: t('plan.s.grid_import'),
      gridExport: t('plan.s.grid_export'),
      soc: t('plan.s.soc'),
      importPrice: t('plan.s.import_price'),
      exportPrice: t('plan.s.export_price'),
      morningBuffer: (pct) => t('plan.line.morning', { soc: tPercent(pct) }),
      eveningTarget: (pct) => t('plan.line.evening', { soc: tPercent(pct) }),
      now: t('plan.now'),
      decision: t('plan.at.marker'),
      actualPv: t('plan.s.actual_pv'),
      actualHouse: t('plan.s.actual_house'),
      actualGrid: t('plan.s.actual_grid'),
      actualBattery: t('plan.s.actual_battery'),
      actualSoc: t('plan.s.actual_soc'),
      comparedSoc: t('plan.s.compared_soc'),
      comparedBattery: t('plan.s.compared_battery'),
      laneName,
      power: (w) => tPower(w),
      percent: (pct) => tPercent(pct),
      price,
      axisTime: (ms) => {
        const c = tClock(new Date(ms));
        return c.hour === 0 && c.minute === 0 ? dayFormat(ms) : tTime(new Date(ms));
      },
      tooltip: tooltipFor,
    };
  }

  onMount(() => {
    if (!el) return;
    chart = init(el, undefined, { renderer: 'canvas' });
    chart.on('datazoom', (p) => {
      const e = p as { batch?: { start: number; end: number }[]; start?: number; end?: number };
      const r = e.batch?.[0] ?? e;
      if (typeof r.start === 'number' && typeof r.end === 'number') {
        zoom = { start: r.start, end: r.end };
      }
    });
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
      if (tip) void unmount(tip);
      chart?.dispose();
      chart = undefined;
    };
  });

  // A new window starts unzoomed.
  let windowKey = '';
  $effect(() => {
    const key = `${win.fromMs}|${win.toMs}`;
    if (key === windowKey) return;
    windowKey = key;
    zoom = { ...FULL };
  });

  // One setOption per change of data, locale, theme or width: ECharts diffs the series. The zoom
  // is read untracked (zooming must not rebuild the option) and handed in so it survives.
  $effect(() => {
    void themeTick;
    if (!chart || !el) return;
    const c = readColors(el);
    colors = c;
    const option = buildOption({
      data,
      lines,
      nowMs,
      colors: c,
      labels: labels(),
      compact,
      window: win,
      actual,
      lanes,
      compare,
      marker,
    }) as { dataZoom: Record<string, unknown>[] };
    const z = untrack(() => zoom);
    option.dataZoom[0] = { ...option.dataZoom[0], start: z.start, end: z.end };
    chart.setOption(option, { notMerge: true });
  });

  function act(action: ZoomAction): void {
    const next = applyZoom(zoom, action);
    zoom = next;
    chart?.dispatchAction({ type: 'dataZoom', start: next.start, end: next.end });
  }

  function onKey(e: KeyboardEvent): void {
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    const action = zoomKey(e.key);
    if (!action) return;
    e.preventDefault();
    act(action);
  }

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

  /** The kinds the Gantt shows, for the key under the chart. */
  const keyKinds = $derived({
    planned: [...new Set(lanes.flatMap((l) => l.planned.map((x) => x.kind)))],
    measured: [...new Set(lanes.flatMap((l) => l.actual.map((x) => x.kind)))],
  });

  // Keep the chart reachable for tests and the text view.
  export function reasonsAt(ms: number): number {
    return indexAt(data.start, ms);
  }
</script>

<div class="toolbar" role="group" aria-label={t('plan.zoom.label')}>
  <button
    type="button"
    class="btn secondary small"
    data-testid="zoom-left"
    aria-label={t('plan.zoom.left')}
    onclick={() => act('left')}>‹</button
  >
  <button
    type="button"
    class="btn secondary small"
    data-testid="zoom-out"
    aria-label={t('plan.zoom.out')}
    onclick={() => act('out')}>−</button
  >
  <button
    type="button"
    class="btn secondary small"
    data-testid="zoom-in"
    aria-label={t('plan.zoom.in')}
    onclick={() => act('in')}>+</button
  >
  <button
    type="button"
    class="btn secondary small"
    data-testid="zoom-right"
    aria-label={t('plan.zoom.right')}
    onclick={() => act('right')}>›</button
  >
  <button
    type="button"
    class="btn secondary small"
    data-testid="zoom-reset"
    disabled={!zoomed}
    onclick={() => act('reset')}>{t('plan.zoom.reset')}</button
  >
  <button
    type="button"
    class="btn secondary small png"
    data-testid="plan-chart-png"
    onclick={download}>{t('history.export.png')}</button
  >
</div>
<p class="zoom-status hint" role="status" aria-live="polite" data-testid="zoom-status">
  {#if zoomed}{t('plan.zoom.showing', {
      from: tDateTime(new Date(visible[0])),
      to: tDateTime(new Date(visible[1])),
    })}{/if}
</p>
<!-- svelte-ignore a11y_no_noninteractive_tabindex, a11y_no_noninteractive_element_interactions (keyboard zoom and pan of the chart) -->
<div
  class="frame"
  role="group"
  tabindex="0"
  aria-label={t('plan.zoom.keys')}
  data-testid="plan-chart-frame"
  onkeydown={onKey}
>
  <div
    bind:this={el}
    class="chart"
    style:height="{height}px"
    role="img"
    aria-label={label}
    aria-describedby={describedby}
    data-testid="plan-chart"
  ></div>
</div>

{#if colors && lanes.length > 0}
  <ul class="key" aria-label={t('plan.key.title')} data-testid="plan-key">
    {#each keyKinds.planned as kind (kind)}
      <li>
        <span class="sw planned" style:--c={segColor(colors, kind)} aria-hidden="true"></span>{t(
          'plan.key.planned',
          { what: t(SEG_LABEL[kind]) },
        )}
      </li>
    {/each}
    {#each keyKinds.measured as kind (kind)}
      <li>
        <span class="sw" style:--c={segColor(colors, kind)} aria-hidden="true"></span>{t(
          'plan.key.measured',
          { what: t(SEG_LABEL[kind]) },
        )}
      </li>
    {/each}
  </ul>
{/if}

<style>
  .toolbar {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    align-items: center;
  }
  .toolbar .btn {
    min-width: 44px;
  }
  .png {
    margin-inline-start: auto;
  }
  .zoom-status {
    margin: 0;
    min-height: 1.45em;
  }
  .frame {
    min-width: 0;
    border-radius: 6px;
  }
  .frame:focus-visible {
    outline: 3px solid var(--primary-color);
    outline-offset: 2px;
  }
  .chart {
    width: 100%;
    min-width: 0;
    touch-action: pan-y;
  }
  .key {
    display: flex;
    flex-wrap: wrap;
    gap: 4px 16px;
    margin: 0;
    padding: 0;
    list-style: none;
    font-size: 0.875rem;
    color: var(--secondary-text-color);
  }
  .sw {
    display: inline-block;
    width: 18px;
    height: 10px;
    margin-inline-end: 6px;
    border-radius: 2px;
    background: var(--c);
    vertical-align: baseline;
  }
  .sw.planned {
    background: color-mix(in srgb, var(--c) 40%, transparent);
    border: 1.5px solid var(--c);
    box-sizing: border-box;
  }
</style>
