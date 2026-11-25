<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  // A finished run: the verdict, what was changed, the comparison with the actual run and the
  // baselines, the charts (ECharts only in the lazy chart chunk), the daily table, the days that
  // were skipped and the honest list of what the replay does not model.
  import type { EChartsCoreOption } from 'echarts/core';
  import type { BacktestRun, BatteryOverlay } from '../api/types';
  import type { HistoryColors } from '../history/chartOptions';
  import { dayLabel } from '../history/labels';
  import {
    hasMessage,
    i18n,
    t,
    tDynamic,
    tNumber,
    tPercent,
    tPower,
    type MessageKey,
  } from '../i18n/index.svelte';
  import { dailyCostOption, gridOption, socOption, type WhatIfText } from '../whatif/chartOptions';
  import {
    changesOf,
    costLadder,
    dayRows,
    metricRows,
    resultCsv,
    resultFilename,
    savingsVsActual,
    verdict,
    windowDays,
    type LadderId,
    type MetricId,
    type MetricKind,
  } from '../whatif/model';

  interface Props {
    run: BacktestRun;
  }
  let { run }: Props = $props();

  const chartModule = import('./HistoryChart.svelte');
  const result = $derived(run.result);
  const totals = $derived(result?.totals ?? null);
  const locale = $derived(i18n.locale);
  const currency = $derived(result?.currency ?? null);

  const money = (v: number | null | undefined): string => {
    if (v == null) return '–';
    const value = tNumber(v, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    return currency ? t('history.money', { value, currency }) : value;
  };
  const kwh = (wh: number | null | undefined): string =>
    wh == null ? '–' : t('unit.kwh', { value: tNumber(wh / 1000, { maximumFractionDigits: 1 }) });
  const ratio = (v: number | null | undefined): string => (v == null ? '–' : tPercent(v * 100));
  const FORMAT: Record<MetricKind, (v: number | null) => string> = {
    money,
    energy: kwh,
    ratio,
    cycles: (v) => (v == null ? '–' : tNumber(v, { maximumFractionDigits: 1 })),
  };
  const signed = (v: number): string => {
    const r = Math.round(v * 100) / 100 || 0;
    return `${r > 0 ? '+' : ''}${money(r)}`;
  };

  const METRIC: Record<MetricId, MessageKey> = {
    cost_net: 'whatif.metric.cost_net',
    import_wh: 'history.s.import',
    export_wh: 'history.s.export',
    charge_wh: 'whatif.metric.charge',
    discharge_wh: 'whatif.metric.discharge',
    autarky: 'history.tile.autarky',
    self_consumption: 'history.tile.self_consumption',
    cycles: 'history.s.cycles',
  };
  const LADDER: Record<LadderId, MessageKey> = {
    no_pv: 'history.s.baseline_no_pv',
    pv_only: 'history.s.baseline_pv_only',
    actual: 'whatif.col.actual',
    whatif: 'whatif.col.whatif',
    perfect: 'whatif.col.perfect',
  };
  const BATTERY: Record<keyof BatteryOverlay, MessageKey> = {
    strategy: 'bat.strategy',
    autarky_weight_per_kwh: 'whatif.battery.autarky',
    capacity_wh: 'energy.path.battery_capacity',
    max_charge_w: 'whatif.battery.max_charge',
    max_discharge_w: 'whatif.battery.max_discharge',
    reserve_pct: 'bat.reserve',
    grid_charge: 'whatif.battery.grid_charge',
  };
  const STRATEGY: Record<string, MessageKey> = {
    self_consumption: 'bat.strategy.self_consumption',
    prefer_full: 'bat.strategy.prefer_full',
    target_by_time: 'bat.strategy.target_by_time',
  };

  function batteryValue(field: keyof BatteryOverlay, value: string | number | boolean): string {
    if (field === 'strategy') return t(STRATEGY[String(value)] ?? 'whatif.as_configured');
    if (field === 'capacity_wh') return kwh(Number(value));
    if (field === 'max_charge_w' || field === 'max_discharge_w') return tPower(Number(value));
    if (field === 'reserve_pct') return tPercent(Number(value));
    if (field === 'grid_charge') {
      return value === false
        ? t('whatif.battery.grid.off')
        : t('whatif.change.grid_on', {
            price: tNumber(Number(value), { maximumFractionDigits: 4 }),
          });
    }
    return tNumber(Number(value), { maximumFractionDigits: 4 });
  }

  const changes = $derived(run.request ? changesOf(run.request, result) : []);
  const paramName = (key: string): string =>
    hasMessage(`param.${key}.name`) ? tDynamic(`param.${key}.name`, undefined) : key;
  const num = (v: unknown): string =>
    typeof v === 'number' ? tNumber(v, { maximumFractionDigits: 6 }) : String(v ?? '');

  const delta = $derived(totals ? savingsVsActual(totals) : 0);
  const kind = $derived(verdict(delta));
  const metrics = $derived(totals ? metricRows(totals) : []);
  const ladder = $derived(totals ? costLadder(totals) : []);
  const days = $derived(result ? dayRows(result.days) : []);
  const hasPerfect = $derived(totals?.perfect != null);
  const dayCount = $derived(result ? windowDays(result.start, result.end) : 0);

  const whenFormat = $derived(
    new Intl.DateTimeFormat(locale, {
      day: 'numeric',
      month: 'short',
      hour: '2-digit',
      minute: '2-digit',
      timeZone: i18n.timeZone ?? result?.tz,
    }),
  );
  const text = $derived<WhatIfText>({
    actual: t('whatif.col.actual'),
    whatif: t('whatif.col.whatif'),
    perfect: t('whatif.col.perfect'),
    money,
    power: tPower,
    percent: tPercent,
    day: (d) => dayLabel(d, locale),
    when: (iso) => whenFormat.format(new Date(iso)),
  });

  function downloadCsv(): void {
    if (!result) return;
    const blob = new Blob([resultCsv(result)], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = resultFilename(result, 'csv');
    a.click();
    URL.revokeObjectURL(url);
  }
  const file = (name: string): string => `suncore-whatif-${name}-${result?.start}_${result?.end}`;
</script>

{#snippet chartCard(
  id: string,
  title: string,
  hint: string,
  label: string,
  build: (colors: HistoryColors, compact: boolean) => EChartsCoreOption,
)}
  <section class="card stack" aria-labelledby="wi-{id}" data-testid="whatif-chart-{id}">
    <h3 id="wi-{id}">{title}</h3>
    <p class="hint">{hint}</p>
    {#await chartModule}
      <p class="hint" role="status">{t('plan.chart.loading')}</p>
    {:then mod}
      {@const Chart = mod.default}
      <div class="chart">
        <Chart testid="whatif-plot-{id}" {label} filename={file(id)} {build} />
      </div>
    {:catch}
      <p class="notice error" role="alert">{t('plan.chart.error')}</p>
    {/await}
  </section>
{/snippet}

<section class="result stack" aria-labelledby="wi-result" data-testid="whatif-result">
  <header class="head">
    <h3 id="wi-result">{t('whatif.result.title')}</h3>
    <p class="hint">
      {t('history.range', {
        from: dayLabel(run.start, locale),
        to: dayLabel(run.end, locale),
      })} · {t('whatif.period.days', { count: dayCount })}
    </p>
  </header>

  {#if !result || !totals}
    <p class="notice warn" data-testid="whatif-no-result">{t('whatif.result.empty')}</p>
  {:else}
    <div class="card stack verdict" data-testid="whatif-verdict" data-verdict={kind}>
      <p class="big">
        {t(`whatif.verdict.${kind}` as MessageKey, { amount: money(Math.abs(delta)) })}
      </p>
      <p class="hint">{t('whatif.result.label')}</p>
      <div class="actions">
        <button type="button" class="btn secondary" data-testid="whatif-csv" onclick={downloadCsv}>
          {t('history.export.csv')}
        </button>
      </div>
    </div>

    <section class="card stack" aria-labelledby="wi-limits" data-testid="whatif-limitations">
      <h3 id="wi-limits">{t('whatif.limits.title')}</h3>
      <ul>
        {#each result.limitations as l, i (i)}
          <li>{tDynamic(l.key, l.args)}</li>
        {/each}
      </ul>
      {#if result.stats.fallbacks > 0}
        <p class="notice warn" data-testid="whatif-fallbacks">
          {t('whatif.stats.fallback', { count: result.stats.fallbacks })}
        </p>
      {/if}
    </section>

    <section class="card stack" aria-labelledby="wi-changes" data-testid="whatif-changes">
      <h3 id="wi-changes">{t('whatif.changes.title')}</h3>
      {#if changes.length === 0}
        <p>{t('whatif.changes.none')}</p>
      {:else}
        <ul>
          {#each changes as c, i (i)}
            <li>
              {#if c.kind === 'param'}
                {#if c.used !== c.requested}
                  {t('whatif.change.param_used', {
                    name: paramName(c.key),
                    requested: num(c.requested),
                    used: num(c.used),
                  })}
                {:else}
                  {t('whatif.change.pair', { name: paramName(c.key), value: num(c.requested) })}
                {/if}
              {:else if c.kind === 'battery'}
                {t('whatif.change.pair', {
                  name: t(BATTERY[c.field]),
                  value: batteryValue(c.field, c.value),
                })}
              {:else if c.kind === 'tariff'}
                {t('whatif.change.tariff', {
                  import: tNumber(c.importPrice, { maximumFractionDigits: 4 }),
                  export: tNumber(c.exportPrice, { maximumFractionDigits: 4 }),
                })}
              {:else}
                {t('whatif.change.perfect')}
              {/if}
            </li>
          {/each}
        </ul>
      {/if}
    </section>

    <section class="card stack" aria-labelledby="wi-compare">
      <h3 id="wi-compare">{t('whatif.table.title')}</h3>
      <!-- svelte-ignore a11y_no_noninteractive_tabindex (keyboard access to a scrollable table) -->
      <div class="scroll" role="region" tabindex="0" aria-labelledby="wi-compare">
        <table data-testid="whatif-metrics">
          <thead>
            <tr>
              <th scope="col">{t('whatif.table.metric')}</th>
              <th scope="col">{t('whatif.col.actual')}</th>
              <th scope="col">{t('whatif.col.whatif')}</th>
              {#if hasPerfect}<th scope="col">{t('whatif.col.perfect')}</th>{/if}
            </tr>
          </thead>
          <tbody>
            {#each metrics as m (m.id)}
              <tr data-metric={m.id}>
                <th scope="row">{t(METRIC[m.id])}</th>
                <td>{FORMAT[m.kind](m.actual)}</td>
                <td>{FORMAT[m.kind](m.whatif)}</td>
                {#if hasPerfect}<td>{FORMAT[m.kind](m.perfect)}</td>{/if}
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    </section>

    <section class="card stack" aria-labelledby="wi-ladder" data-testid="whatif-ladder">
      <h3 id="wi-ladder">{t('whatif.ladder.title')}</h3>
      <dl class="tiles">
        {#each ladder as row (row.id)}
          <div class="tile" data-ladder={row.id}>
            <dt>{t(LADDER[row.id])}</dt>
            <dd>{money(row.cost)}</dd>
          </div>
        {/each}
      </dl>
    </section>

    {@render chartCard(
      'cost',
      t('whatif.charts.cost'),
      t('whatif.charts.cost_hint'),
      t('whatif.charts.cost'),
      (colors, compact) => dailyCostOption(result.days, { colors, text, compact }),
    )}
    {@render chartCard(
      'soc',
      t('whatif.charts.soc'),
      t('whatif.charts.hourly_hint'),
      t('whatif.charts.soc'),
      (colors, compact) => socOption(result.hourly, { colors, text, compact }),
    )}
    {@render chartCard(
      'grid',
      t('whatif.charts.grid'),
      t('whatif.charts.grid_hint'),
      t('whatif.charts.grid'),
      (colors, compact) => gridOption(result.hourly, { colors, text, compact }),
    )}

    <section class="card stack" aria-labelledby="wi-days">
      <h3 id="wi-days">{t('whatif.days.title')}</h3>
      <!-- svelte-ignore a11y_no_noninteractive_tabindex (keyboard access to a scrollable table) -->
      <div class="scroll" role="region" tabindex="0" aria-labelledby="wi-days">
        <table data-testid="whatif-days-table">
          <thead>
            <tr>
              <th scope="col">{t('history.period.day')}</th>
              <th scope="col">{t('whatif.col.actual')}</th>
              <th scope="col">{t('whatif.col.whatif')}</th>
              <th scope="col">{t('whatif.days.delta')}</th>
              {#if hasPerfect}<th scope="col">{t('whatif.col.perfect')}</th>{/if}
            </tr>
          </thead>
          <tbody>
            {#each days as d (d.day)}
              <tr>
                <th scope="row">{dayLabel(d.day, locale)}</th>
                <td>{money(d.actual)}</td>
                <td>{money(d.whatif)}</td>
                <td>{signed(d.delta)}</td>
                {#if hasPerfect}<td>{money(d.perfect)}</td>{/if}
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
      <p class="hint">{t('whatif.days.delta_hint')}</p>
    </section>
  {/if}

  {#if result && result.skipped.length > 0}
    <section class="card stack" aria-labelledby="wi-skipped" data-testid="whatif-skipped">
      <h3 id="wi-skipped">{t('whatif.skipped.title')}</h3>
      <ul>
        {#each result.skipped as s (s.day)}
          <li>{dayLabel(s.day, locale, true)}: {tDynamic(s.reason.key, s.reason.args)}</li>
        {/each}
      </ul>
    </section>
  {/if}
</section>

<style>
  .result {
    min-width: 0;
  }
  .big {
    margin: 0;
    font-size: 1.25rem;
    font-weight: 700;
  }
  .actions {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
  }
  ul {
    margin: 0;
    padding-left: 20px;
  }
  .chart {
    display: grid;
    gap: 8px;
    min-width: 0;
  }
  .scroll {
    overflow-x: auto;
  }
  table {
    border-collapse: collapse;
    width: 100%;
    min-width: 22rem;
    font-size: 0.9375rem;
    font-variant-numeric: tabular-nums;
  }
  th,
  td {
    text-align: left;
    padding: 5px 10px 5px 0;
    border-top: 1px solid var(--divider-color);
  }
  thead th {
    border-top: 0;
  }
  .tiles {
    display: grid;
    gap: 12px;
    grid-template-columns: repeat(auto-fill, minmax(9.5rem, 1fr));
    margin: 0;
  }
  .tile {
    display: grid;
    gap: 4px;
    padding: 12px 14px;
    border: 1px solid var(--divider-color);
    border-radius: var(--sc-radius);
    min-width: 0;
  }
  .tile dt {
    color: var(--secondary-text-color);
    font-size: 0.875rem;
  }
  .tile dd {
    margin: 0;
    font-size: 1.25rem;
    font-weight: 700;
    font-variant-numeric: tabular-nums;
  }
</style>
