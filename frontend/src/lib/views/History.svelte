<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import { onMount, untrack } from 'svelte';
  import { historyApi, type HistoryEndpoint } from '../api/history';
  import type { Granularity, HistoryCycles, HistorySummary } from '../api/types';
  import GridEventsSection from '../components/GridEventsSection.svelte';
  import HistorySection from '../components/HistorySection.svelte';
  import {
    costOption,
    cyclesOption,
    energyOption,
    heatmapOption,
    sankeyOption,
    skillOption,
    type HistoryText,
  } from '../history/chartOptions';
  import {
    bucketLabel,
    dayLabel,
    hourLabel,
    monthLabel,
    weekdayLabel,
    weekNumber,
  } from '../history/labels';
  import {
    PERIODS,
    addDays,
    dayOf,
    hasData,
    historyHash,
    incompleteDays,
    isDay,
    parseHistoryQuery,
    planDayHash,
  } from '../history/model';
  import {
    i18n,
    t,
    tDynamic,
    tNumber,
    tPercent,
    tPower,
    type MessageKey,
  } from '../i18n/index.svelte';
  import { router } from '../router.svelte';
  import { HistoryStore } from '../stores/history.svelte';

  const uid = $props.id();
  const store = new HistoryStore();
  store.apply(parseHistoryQuery(router.query));
  // Chart code is its own chunk (shared with the plan chart) and starts loading with the view.
  const chartModule = import('../components/HistoryChart.svelte');

  const PERIOD_LABEL: Record<Granularity, MessageKey> = {
    day: 'history.period.day',
    week: 'history.period.week',
    month: 'history.period.month',
    year: 'history.period.year',
  };
  const HORIZON: Record<string, MessageKey> = {
    '0_6h': 'fc.horizon.0_6h',
    '6_24h': 'fc.horizon.6_24h',
    '24_48h': 'fc.horizon.24_48h',
    '48h_plus': 'fc.horizon.48h_plus',
  };

  // Navigation is the state of the URL: shareable, and the back button works across visits.
  $effect(() => {
    const hash = historyHash(store.state);
    untrack(() => {
      if (location.hash !== hash) router.replace(hash);
      void store.load();
    });
  });
  // A period that includes today moves on while the page is open.
  onMount(() => {
    const id = setInterval(() => {
      if (store.summary?.in_progress && !store.loading) void store.load();
    }, 300_000);
    return () => clearInterval(id);
  });

  const summary = $derived(store.summary);
  const empty = $derived(summary !== null && !hasData(summary));
  const locale = $derived(i18n.locale);

  // --- formatting ------------------------------------------------------------------------
  const kwh = (wh: number | null | undefined): string =>
    wh == null ? '–' : t('unit.kwh', { value: tNumber(wh / 1000, { maximumFractionDigits: 1 }) });
  const ratio = (v: number | null | undefined): string => (v == null ? '–' : tPercent(v * 100));
  const money = (v: number | null | undefined, currency: string | null | undefined): string =>
    v == null || !currency
      ? '–'
      : t('history.money', {
          value: tNumber(v, { minimumFractionDigits: 2, maximumFractionDigits: 2 }),
          currency,
        });
  const hours = (h: number | null | undefined): string =>
    h == null ? '–' : t('history.hours', { value: tNumber(h, { maximumFractionDigits: 1 }) });
  const cyclesText = (v: number | null | undefined): string =>
    v == null ? '–' : tNumber(v, { maximumFractionDigits: 1 });

  const currency = $derived(summary?.cost.currency ?? null);
  const text = $derived<HistoryText>({
    series: {
      pv: t('history.s.pv'),
      house: t('history.s.house'),
      import: t('history.s.import'),
      export: t('history.s.export'),
      costNet: t('history.s.cost_net'),
      baselineNoPv: t('history.s.baseline_no_pv'),
      baselinePvOnly: t('history.s.baseline_pv_only'),
      cycles: t('history.s.cycles'),
      socMin: t('history.s.soc_min'),
      socMax: t('history.s.soc_max'),
    },
    node: {
      pv: t('history.s.pv'),
      grid_import: t('history.s.import'),
      battery_discharge: t('history.node.battery_discharge'),
      house: t('history.node.house'),
      battery_charge: t('history.node.battery_charge'),
      grid_export: t('history.s.export'),
    },
    incomplete: t('history.incomplete.tooltip'),
    energy: (wh) => kwh(wh),
    money: (v) => money(v, currency ?? ''),
    power: tPower,
    percent: tPercent,
    number: (v) => tNumber(v, { maximumFractionDigits: 2 }),
    bucket: (key) => bucketLabel(key, locale),
    day: (day) => dayLabel(day, locale),
    weekday: (i) => weekdayLabel(i, locale),
    hour: (h) => hourLabel(h, locale),
    skillName: (source, kind, horizon) =>
      t('history.skill.name', {
        source,
        kind: t(kind === 'pv' ? 'history.s.pv' : 'history.skill.load'),
        horizon: t(HORIZON[horizon] ?? 'fc.horizon.0_6h'),
      }),
  });

  function periodTitle(s: HistorySummary): string {
    const start = dayOf(s.start);
    if (s.period === 'day') return dayLabel(start, locale, true);
    if (s.period === 'month') return monthLabel(start, locale, true);
    if (s.period === 'year') return start.slice(0, 4);
    return t('history.week_title', {
      week: weekNumber(s.key) ?? 0,
      from: dayLabel(start, locale),
      to: dayLabel(addDays(dayOf(s.end), -1), locale),
    });
  }

  const rangeText = (r: { from: string; to: string } | null): string =>
    r ? t('history.range', { from: dayLabel(r.from, locale), to: dayLabel(r.to, locale) }) : '';

  // --- tiles -----------------------------------------------------------------------------
  interface Tile {
    key: string;
    label: MessageKey;
    value: string;
  }
  const tiles = $derived.by<Tile[]>(() => {
    const s = summary;
    if (!s) return [];
    const c = s.cost;
    return [
      { key: 'autarky', label: 'history.tile.autarky', value: ratio(s.kpis.autarky) },
      {
        key: 'self_consumption',
        label: 'history.tile.self_consumption',
        value: ratio(s.kpis.self_consumption),
      },
      { key: 'pv', label: 'history.s.pv', value: kwh(s.energy.pv_wh) },
      { key: 'house', label: 'history.s.house', value: kwh(s.energy.house_wh) },
      { key: 'import', label: 'history.s.import', value: kwh(s.energy.import_wh) },
      { key: 'export', label: 'history.s.export', value: kwh(s.energy.export_wh) },
      { key: 'cost', label: 'history.tile.cost', value: money(c.cost_net, c.currency) },
      {
        key: 'savings_no_pv',
        label: 'history.tile.savings_no_pv',
        value: money(c.savings_vs_no_pv, c.currency),
      },
      {
        key: 'savings_pv_only',
        label: 'history.tile.savings_pv_only',
        value: money(c.savings_vs_pv_only, c.currency),
      },
      {
        key: 'cycles',
        label: 'history.tile.cycles',
        value: cyclesText(s.kpis.equivalent_full_cycles),
      },
    ];
  });

  const hasCost = $derived(summary?.cost.cost_net != null);
  const dayBuckets = $derived(summary?.buckets.some((b) => b.key.length === 10) ?? false);

  /** Mean error of a skill series, weighted by the samples, for its caption. */
  const skillRows = $derived(
    (store.skill?.series ?? []).map((s) => ({
      name: text.skillName(s.source_id, s.kind, s.horizon),
      mae: s.mae_w,
      bias: s.bias_w,
      n: s.n,
    })),
  );

  const maxRun = $derived(
    Math.max(1, ...(store.devices?.devices ?? []).map((d) => d.run_hours ?? 0)),
  );
  const cyclesTotal = (c: HistoryCycles | null): string =>
    cyclesText(c?.total_equivalent_full_cycles);

  const file = (name: string, key: string): string => `suncore-${name}-${key}`;
  const csv = (e: HistoryEndpoint): string => historyApi.csvUrl(e, store.paramsFor(e));
  const sectionError = (k: keyof typeof store.errors): string | null => store.errors[k] ?? null;

  function onDate(e: Event & { currentTarget: HTMLInputElement }): void {
    const v = e.currentTarget.value;
    if (isDay(v)) store.date = v;
  }
</script>

<section class="history" aria-labelledby="{uid}-title" data-testid="history-view">
  <header class="head">
    <h2 id="{uid}-title">{t('history.title')}</h2>
  </header>
  <p class="hint">{t('history.intro')}</p>

  <div class="card controls">
    <div class="range" role="group" aria-label={t('plan.range.label')}>
      {#each PERIODS as p (p)}
        <button
          type="button"
          class="seg-btn"
          aria-pressed={store.period === p}
          data-testid="period-{p}"
          onclick={() => store.setPeriod(p)}>{t(PERIOD_LABEL[p])}</button
        >
      {/each}
    </div>
    <div class="nav" role="group" aria-label={t('history.nav.label')}>
      <button
        type="button"
        class="btn secondary"
        disabled={!summary}
        data-testid="period-prev"
        onclick={() => store.step(-1)}>{t('history.nav.prev')}</button
      >
      <button
        type="button"
        class="btn secondary"
        disabled={!summary || summary.in_progress}
        data-testid="period-next"
        onclick={() => store.step(1)}>{t('history.nav.next')}</button
      >
      <button
        type="button"
        class="btn secondary"
        disabled={store.date === null}
        data-testid="period-today"
        onclick={() => store.today()}>{t('history.nav.today')}</button
      >
    </div>
    <div class="field jump">
      <label for="{uid}-date">{t('history.nav.date')}</label>
      <input
        id="{uid}-date"
        type="date"
        class="input"
        value={store.date ?? (summary ? dayOf(summary.start) : '')}
        data-testid="period-date"
        onchange={onDate}
      />
    </div>
  </div>

  {#if store.unavailable}
    <p class="card notice error" role="alert" data-testid="history-unavailable">
      {t('history.unavailable')}
    </p>
  {:else if sectionError('summary')}
    <p class="notice error" role="alert" data-testid="history-error">
      {tDynamic(sectionError('summary') ?? '', undefined, 'api.error.unknown')}
    </p>
  {:else if !summary}
    <p class="hint" role="status">{t('wizard.loading')}</p>
  {:else}
    <div class="period" data-testid="history-period" data-key={summary.key}>
      <h3 aria-live="polite">{periodTitle(summary)}</h3>
      <ul class="badges" aria-label={t('history.status.label')}>
        {#if summary.in_progress}
          <li class="chip-tag" data-testid="badge-in-progress">{t('history.badge.in_progress')}</li>
        {/if}
        {#if incompleteDays(summary) > 0}
          <li class="chip-tag warn" data-testid="badge-incomplete">
            {t('history.badge.incomplete', { count: incompleteDays(summary) })}
          </li>
        {/if}
        {#if summary.derived_share != null && summary.derived_share > 0}
          <li class="chip-tag" data-testid="badge-derived">
            {t('history.badge.derived', { share: tPercent(summary.derived_share * 100) })}
          </li>
        {/if}
        {#if summary.period === 'day'}
          <li>
            <a
              class="chip-tag link"
              href={planDayHash(dayOf(summary.start))}
              data-testid="plan-link">{t('history.plan_link')}</a
            >
          </li>
        {/if}
      </ul>
    </div>

    {#if empty}
      <div class="card stack" data-testid="history-empty">
        <h3>{t('history.empty.title')}</h3>
        <p>{t('history.empty.body')}</p>
      </div>
    {:else}
      {#if incompleteDays(summary) > 0}
        <p class="notice warn" data-testid="history-incomplete-note">
          {t('history.incomplete.note')}
        </p>
      {/if}

      <dl class="tiles" data-testid="history-tiles">
        {#each tiles as tile (tile.key)}
          <div class="tile" data-testid="tile-{tile.key}">
            <dt>{t(tile.label)}</dt>
            <dd>{tile.value}</dd>
          </div>
        {/each}
      </dl>

      <div class="card stack" data-testid="history-attribution">
        <h3>{t('history.attr.title')}</h3>
        {#if hasCost}
          <p>
            {t('history.attr.body', {
              total: money(summary.cost.savings_vs_no_pv, currency),
              plant: money(summary.cost.pv_contribution, currency),
              suncore: money(summary.cost.savings_vs_pv_only, currency),
            })}
          </p>
          <p class="hint">
            {t('history.attr.priced', { priced: summary.cost.priced_days, days: summary.days })}
          </p>
        {:else}
          <p class="hint">{t('history.attr.no_prices')}</p>
        {/if}
        <p class="hint">{t('history.attr.limits')}</p>
      </div>

      <HistorySection
        id="energy"
        title={t('history.energy.title')}
        hint={t('history.energy.hint')}
        csvHref={csv('summary')}
        csvLabel={t('history.export.csv')}
        tableSummary={t('plan.table.summary')}
      >
        {#snippet chart()}
          {#await chartModule}
            <p class="hint" role="status">{t('plan.chart.loading')}</p>
          {:then mod}
            {@const Chart = mod.default}
            <Chart
              testid="chart-energy"
              label={t('history.energy.label')}
              filename={file('energy', summary.key)}
              build={(colors, compact) => energyOption(summary.buckets, { colors, text, compact })}
            />
          {:catch}
            <p class="notice error" role="alert">{t('plan.chart.error')}</p>
          {/await}
        {/snippet}
        {#snippet table()}
          <table data-testid="table-energy">
            <caption class="sr-only">{t('history.energy.title')}</caption>
            <thead>
              <tr>
                <th scope="col">{t('history.table.period')}</th>
                <th scope="col">{t('history.s.pv')}</th>
                <th scope="col">{t('history.s.house')}</th>
                <th scope="col">{t('history.s.import')}</th>
                <th scope="col">{t('history.s.export')}</th>
                <th scope="col">{t('history.tile.autarky')}</th>
                {#if dayBuckets}<th scope="col">{t('history.table.plan')}</th>{/if}
              </tr>
            </thead>
            <tbody>
              {#each summary.buckets as b (b.key)}
                <tr data-complete={b.complete}>
                  <th scope="row">
                    {bucketLabel(b.key, locale)}
                    {#if !b.complete}<span class="chip-tag warn"
                        >{t('history.incomplete.short')}</span
                      >{/if}
                  </th>
                  <td>{kwh(b.energy.pv_wh)}</td>
                  <td>{kwh(b.energy.house_wh)}</td>
                  <td>{kwh(b.energy.import_wh)}</td>
                  <td>{kwh(b.energy.export_wh)}</td>
                  <td>{ratio(b.kpis.autarky)}</td>
                  {#if dayBuckets}
                    <td>
                      {#if b.key.length === 10}
                        <a href={planDayHash(b.key)} data-testid="plan-link-day"
                          >{t('history.plan_link.short', { day: dayLabel(b.key, locale) })}</a
                        >
                      {/if}
                    </td>
                  {/if}
                </tr>
              {/each}
            </tbody>
          </table>
        {/snippet}
      </HistorySection>

      <HistorySection
        id="cost"
        title={t('history.cost.title')}
        hint={t('history.cost.hint')}
        csvHref={csv('summary')}
        csvLabel={t('history.export.csv')}
        tableSummary={t('plan.table.summary')}
        empty={hasCost ? null : t('history.attr.no_prices')}
      >
        {#snippet chart()}
          {#await chartModule then mod}
            {@const Chart = mod.default}
            <Chart
              testid="chart-cost"
              label={t('history.cost.label')}
              filename={file('cost', summary.key)}
              build={(colors, compact) => costOption(summary.buckets, { colors, text, compact })}
            />
          {/await}
        {/snippet}
        {#snippet table()}
          <table data-testid="table-cost">
            <caption class="sr-only">{t('history.cost.title')}</caption>
            <thead>
              <tr>
                <th scope="col">{t('history.table.period')}</th>
                <th scope="col">{t('history.s.cost_net')}</th>
                <th scope="col">{t('history.s.baseline_pv_only')}</th>
                <th scope="col">{t('history.s.baseline_no_pv')}</th>
                <th scope="col">{t('history.tile.savings_pv_only')}</th>
              </tr>
            </thead>
            <tbody>
              {#each summary.buckets as b (b.key)}
                <tr>
                  <th scope="row">{bucketLabel(b.key, locale)}</th>
                  <td>{money(b.cost.cost_net, b.cost.currency)}</td>
                  <td>{money(b.cost.baseline_pv_only_cost, b.cost.currency)}</td>
                  <td>{money(b.cost.baseline_no_pv_cost, b.cost.currency)}</td>
                  <td>{money(b.cost.savings_vs_pv_only, b.cost.currency)}</td>
                </tr>
              {/each}
            </tbody>
          </table>
        {/snippet}
      </HistorySection>

      <HistorySection
        id="sankey"
        title={t('history.sankey.title')}
        hint={t('history.sankey.hint')}
        csvHref={csv('sankey')}
        csvLabel={t('history.export.csv')}
        tableSummary={t('plan.table.summary')}
        error={sectionError('sankey')}
      >
        {#snippet chart()}
          {#if store.sankey}
            {@const data = store.sankey}
            {#await chartModule then mod}
              {@const Chart = mod.default}
              <Chart
                testid="chart-sankey"
                label={t('history.sankey.label')}
                filename={file('sankey', data.key)}
                build={(colors, compact) => sankeyOption(data, { colors, text, compact })}
              />
            {/await}
          {/if}
        {/snippet}
        {#snippet table()}
          {#if store.sankey}
            <table data-testid="table-sankey">
              <caption class="sr-only">{t('history.sankey.title')}</caption>
              <thead>
                <tr>
                  <th scope="col">{t('history.sankey.from')}</th>
                  <th scope="col">{t('history.sankey.to')}</th>
                  <th scope="col">{t('history.sankey.energy')}</th>
                </tr>
              </thead>
              <tbody>
                {#each store.sankey.links as l (l.source + l.target)}
                  <tr>
                    <th scope="row">{text.node[l.source]}</th>
                    <td>{text.node[l.target]}</td>
                    <td>{kwh(l.wh)}</td>
                  </tr>
                {/each}
              </tbody>
            </table>
            <p class="hint">{t('history.sankey.rule')}</p>
          {/if}
        {/snippet}
      </HistorySection>

      <HistorySection
        id="heatmap"
        title={t('history.heatmap.title')}
        hint={t('history.heatmap.hint')}
        caption={rangeText(store.heat)}
        csvHref={csv('heatmap')}
        csvLabel={t('history.export.csv')}
        tableSummary={t('plan.table.summary')}
        error={sectionError('heatmap')}
      >
        {#snippet chart()}
          {#if store.heatmap}
            {@const data = store.heatmap}
            {#await chartModule then mod}
              {@const Chart = mod.default}
              <Chart
                testid="chart-heatmap"
                label={t('history.heatmap.label')}
                filename={file('heatmap', summary.key)}
                height={340}
                build={(colors, compact) => heatmapOption(data, { colors, text, compact })}
              />
            {/await}
          {/if}
        {/snippet}
        {#snippet table()}
          {#if store.heatmap}
            <!-- svelte-ignore a11y_no_noninteractive_tabindex (keyboard access to a scrollable table) -->
            <div class="scroll" role="region" tabindex="0" aria-label={t('history.heatmap.title')}>
              <table data-testid="table-heatmap">
                <caption class="sr-only">{t('history.heatmap.title')}</caption>
                <thead>
                  <tr>
                    <th scope="col">{t('history.heatmap.hour')}</th>
                    {#each store.heatmap.values as _row, d (d)}
                      <th scope="col">{weekdayLabel(d, locale)}</th>
                    {/each}
                  </tr>
                </thead>
                <tbody>
                  {#each Array.from({ length: 24 }, (_, h) => h) as h (h)}
                    <tr>
                      <th scope="row">{hourLabel(h, locale)}</th>
                      {#each store.heatmap.values as row, d (d)}
                        <td>{row[h] == null ? '–' : tPower(row[h])}</td>
                      {/each}
                    </tr>
                  {/each}
                </tbody>
              </table>
            </div>
          {/if}
        {/snippet}
      </HistorySection>

      <HistorySection
        id="cycles"
        title={t('history.cycles.title')}
        hint={t('history.cycles.hint')}
        caption={`${rangeText(store.trend)} · ${t('history.cycles.total', { value: cyclesTotal(store.cycles) })}`}
        csvHref={csv('cycles')}
        csvLabel={t('history.export.csv')}
        tableSummary={t('plan.table.summary')}
        error={sectionError('cycles')}
        empty={store.cycles && store.cycles.total_equivalent_full_cycles == null
          ? t('history.none')
          : null}
      >
        {#snippet chart()}
          {#if store.cycles}
            {@const data = store.cycles}
            {#await chartModule then mod}
              {@const Chart = mod.default}
              <Chart
                testid="chart-cycles"
                label={t('history.cycles.label')}
                filename={file('cycles', summary.key)}
                build={(colors, compact) => cyclesOption(data, { colors, text, compact })}
              />
            {/await}
          {/if}
        {/snippet}
        {#snippet table()}
          {#if store.cycles}
            <!-- svelte-ignore a11y_no_noninteractive_tabindex (keyboard access to a scrollable table) -->
            <div class="scroll" role="region" tabindex="0" aria-label={t('history.cycles.title')}>
              <table data-testid="table-cycles">
                <caption class="sr-only">{t('history.cycles.title')}</caption>
                <thead>
                  <tr>
                    <th scope="col">{t('history.table.period')}</th>
                    <th scope="col">{t('history.s.cycles')}</th>
                    <th scope="col">{t('history.cycles.charged')}</th>
                    <th scope="col">{t('history.cycles.discharged')}</th>
                    <th scope="col">{t('history.s.soc_min')}</th>
                    <th scope="col">{t('history.s.soc_max')}</th>
                  </tr>
                </thead>
                <tbody>
                  {#each store.cycles.days as d (d.day)}
                    <tr>
                      <th scope="row">
                        {dayLabel(d.day, locale)}
                        {#if !d.complete}<span class="chip-tag warn"
                            >{t('history.incomplete.short')}</span
                          >{/if}
                      </th>
                      <td>{cyclesText(d.equivalent_full_cycles)}</td>
                      <td>{kwh(d.charge_wh)}</td>
                      <td>{kwh(d.discharge_wh)}</td>
                      <td>{d.soc_min_pct == null ? '–' : tPercent(d.soc_min_pct)}</td>
                      <td>{d.soc_max_pct == null ? '–' : tPercent(d.soc_max_pct)}</td>
                    </tr>
                  {/each}
                </tbody>
              </table>
            </div>
          {/if}
        {/snippet}
      </HistorySection>

      <HistorySection
        id="skill"
        title={t('history.skill.title')}
        hint={t('history.skill.hint')}
        caption={rangeText(store.trend)}
        csvHref={csv('forecast-skill')}
        csvLabel={t('history.export.csv')}
        tableSummary={t('plan.table.summary')}
        error={sectionError('skill')}
        empty={store.skill && store.skill.series.length === 0 ? t('history.none') : null}
      >
        {#snippet chart()}
          {#if store.skill}
            {@const data = store.skill}
            {#await chartModule then mod}
              {@const Chart = mod.default}
              <Chart
                testid="chart-skill"
                label={t('history.skill.label')}
                filename={file('forecast-skill', summary.key)}
                build={(colors, compact) => skillOption(data, { colors, text, compact })}
              />
            {/await}
          {/if}
        {/snippet}
        {#snippet table()}
          <table data-testid="table-skill">
            <caption class="sr-only">{t('history.skill.title')}</caption>
            <thead>
              <tr>
                <th scope="col">{t('fc.source')}</th>
                <th scope="col">{t('history.skill.mae')}</th>
                <th scope="col">{t('history.skill.bias')}</th>
                <th scope="col">{t('history.skill.samples')}</th>
              </tr>
            </thead>
            <tbody>
              {#each skillRows as r (r.name)}
                <tr>
                  <th scope="row">{r.name}</th>
                  <td>{r.mae == null ? '–' : tPower(r.mae)}</td>
                  <td>{r.bias == null ? '–' : tPower(r.bias)}</td>
                  <td>{tNumber(r.n)}</td>
                </tr>
              {/each}
            </tbody>
          </table>
        {/snippet}
      </HistorySection>

      <section class="card stack" aria-labelledby="{uid}-devices" data-testid="history-devices">
        <h3 id="{uid}-devices">{t('history.devices.title')}</h3>
        <p class="hint">{t('history.devices.hint')}</p>
        {#if sectionError('devices')}
          <p class="notice error" role="alert">
            {tDynamic(sectionError('devices') ?? '', undefined, 'api.error.unknown')}
          </p>
        {:else if store.devices && store.devices.devices.length === 0}
          <p class="hint">{t('history.none')}</p>
        {:else if store.devices}
          <!-- svelte-ignore a11y_no_noninteractive_tabindex (keyboard access to a scrollable table) -->
          <div class="scroll" role="region" tabindex="0" aria-label={t('history.devices.title')}>
            <table data-testid="table-devices">
              <caption class="sr-only">{t('history.devices.title')}</caption>
              <thead>
                <tr>
                  <th scope="col">{t('log.device')}</th>
                  <th scope="col">{t('history.devices.run')}</th>
                  <th scope="col">{t('history.devices.energy')}</th>
                  <th scope="col">{t('history.devices.starts')}</th>
                </tr>
              </thead>
              <tbody>
                {#each store.devices.devices as d (d.device_id)}
                  <tr data-testid="device-row">
                    <th scope="row">{d.name}</th>
                    <td>
                      <span class="bar" aria-hidden="true">
                        <span style:width="{((d.run_hours ?? 0) / maxRun) * 100}%"></span>
                      </span>
                      {hours(d.run_hours)}
                    </td>
                    <td>{kwh(d.energy_wh)}</td>
                    <td>{d.starts == null ? '–' : tNumber(d.starts)}</td>
                  </tr>
                {/each}
              </tbody>
            </table>
          </div>
        {/if}
        <a class="btn secondary small csv" href={csv('devices')} download data-testid="csv-devices"
          >{t('history.export.csv')}</a
        >
      </section>

      <GridEventsSection />
    {/if}
  {/if}
</section>

<style>
  .history {
    display: grid;
    gap: var(--sc-gap);
    min-width: 0;
  }
  .controls {
    display: flex;
    flex-wrap: wrap;
    align-items: end;
    gap: 12px 16px;
  }
  .range {
    display: flex;
    border: 1px solid var(--primary-color);
    border-radius: 22px;
    overflow: hidden;
  }
  .seg-btn {
    min-width: 64px;
    min-height: 44px;
    padding: 0 14px;
    font: inherit;
    border: 0;
    border-inline-start: 1px solid var(--primary-color);
    background: transparent;
    color: var(--primary-color);
    cursor: pointer;
  }
  .seg-btn:first-child {
    border-inline-start: 0;
  }
  .seg-btn[aria-pressed='true'] {
    background: var(--primary-color);
    color: var(--text-primary-color);
    font-weight: 700;
  }
  .nav {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
  }
  .jump {
    min-width: 10rem;
  }
  .period {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px 16px;
  }
  .period h3 {
    font-size: 1.25rem;
  }
  .badges {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    list-style: none;
    margin: 0;
    padding: 0;
  }
  .chip-tag.link {
    color: var(--primary-color);
    border-color: var(--primary-color);
    text-decoration: none;
    min-height: 32px;
    padding: 0 12px;
  }
  .tiles {
    display: grid;
    gap: 12px;
    grid-template-columns: repeat(auto-fill, minmax(10.5rem, 1fr));
    margin: 0;
  }
  .tile {
    display: grid;
    align-content: start;
    gap: 4px;
    padding: 12px 14px;
    background: var(--card-background-color);
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
  }
  .tile dd:not(.hint) {
    font-size: 1.5rem;
    font-weight: 700;
    font-variant-numeric: tabular-nums;
  }
  .scroll {
    overflow-x: auto;
  }
  table {
    border-collapse: collapse;
    width: 100%;
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
  .bar {
    display: inline-block;
    width: 6rem;
    height: 10px;
    margin-inline-end: 8px;
    border-radius: 5px;
    background: var(--secondary-background-color);
    vertical-align: middle;
    overflow: hidden;
  }
  .bar span {
    display: block;
    height: 100%;
    background: var(--primary-color);
  }
  .csv {
    justify-self: start;
  }
</style>
