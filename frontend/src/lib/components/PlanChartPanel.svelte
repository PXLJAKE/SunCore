<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  // The chart card of the plan view (loaded on demand with the chart): the look-back controls
  // (a day, the plan as of a time, a comparison with a second plan), the chart, its exports and
  // the text alternatives. The state lives in the URL (`#/plan?day=`, `?at=`, `&compare=`), so a
  // link from the history or the decision log opens exactly this view.
  import { untrack } from 'svelte';
  import { planHistoryApi } from '../api/planHistory';
  import type { PlanView } from '../api/types';
  import {
    i18n,
    t,
    tDateTime,
    tDynamic,
    tNumber,
    tPercent,
    tPower,
    tClock,
    tTime,
    type MessageKey,
  } from '../i18n/index.svelte';
  import { addDays } from '../history/model';
  import { SITE_KEYS, actualIndex, deviceKey, hasValues } from '../plan/actual';
  import { chartIntervals, referenceTime } from '../plan/assemble';
  import { compareRows, compareSeries, hasDelta } from '../plan/compare';
  import { buildLanes, type GanttLane, type GanttSeg, type SegKind } from '../plan/gantt';
  import { buildChartData, targetLines, type ChartData } from '../plan/model';
  import { parseTime, targetHash, targetWindow, todayOf, type PlanTarget } from '../plan/target';
  import { localInputValue, parseLocalInput, zonedMs } from '../plan/zone';
  import { app } from '../stores/app.svelte';
  import { PlanChartStore, type ChartRequest } from '../stores/planChart.svelte';
  import { plan } from '../stores/plan.svelte';
  import { router } from '../router.svelte';
  import Card from '../ui/Card.svelte';
  import Disclosure from '../ui/Disclosure.svelte';
  import Icon from '../ui/Icon.svelte';
  import PlanChart from './PlanChart.svelte';

  interface Props {
    view: PlanView | null;
    target: PlanTarget;
    nowMs: number;
    currency: string;
    /** Id of the chart's hint, for `aria-describedby`. */
    hintId: string;
  }
  let { view, target, nowMs, currency, hintId }: Props = $props();

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

  const store = new PlanChartStore();
  const uid = $props.id();

  const zone = $derived(i18n.timeZone ?? Intl.DateTimeFormat().resolvedOptions().timeZone);
  const compareMs = $derived(parseTime(router.query.get('compare'), zone));
  const win = $derived(targetWindow(target, nowMs, zone));
  const today = $derived(todayOf(nowMs, zone));
  const req = $derived<ChartRequest>({ target, win, nowMs, live: view, compareMs });

  $effect(() => {
    const r = req;
    untrack(() => {
      void store.syncPlan(r);
      void store.syncCompare(r);
    });
  });

  const intervals = $derived(
    chartIntervals(target, win, view, store.snapshot?.plan.intervals ?? null),
  );
  const data: ChartData = $derived(buildChartData(intervals));
  const deviceIds = $derived([...new Set(intervals.flatMap((i) => Object.keys(i.devices ?? {})))]);

  $effect(() => {
    const r = req;
    const ids = deviceIds;
    untrack(() => void store.syncActual(r, ids));
  });

  /** Measured values worth drawing: a window without any measurement shows no overlay. */
  const measured = $derived(
    store.actual &&
      [store.actual.pvW, store.actual.houseW, store.actual.gridW, store.actual.batteryW].some(
        hasValues,
      )
      ? store.actual
      : null,
  );
  const lanes: GanttLane[] = $derived(buildLanes(intervals, measured));
  const compared = $derived(store.compare ? compareSeries(data, store.compare) : null);
  const lines = $derived(targetLines(plan.risk));
  const marker = $derived.by(() => {
    if (target.kind !== 'at') return null;
    const snap = store.snapshot;
    const i = snap?.interval_index != null ? snap.plan.intervals[snap.interval_index] : null;
    return {
      atMs: target.atMs,
      fromMs: i ? Date.parse(i.start) : target.atMs,
      toMs: i ? Date.parse(i.end) : target.atMs,
    };
  });

  const deviceName = (id: string): string =>
    app.devices?.devices?.find((d) => d.id === id)?.name ?? id;
  const refMs = $derived(referenceTime(target, win, nowMs));
  const multiDay = $derived(win.toMs - win.fromMs > 26 * 3_600_000);
  const when = (ms: number): string => (multiDay ? tDateTime(new Date(ms)) : tTime(new Date(ms)));
  const dayText = (day: string): string =>
    new Intl.DateTimeFormat(i18n.locale, {
      weekday: 'long',
      day: 'numeric',
      month: 'long',
      year: 'numeric',
      timeZone: zone,
    }).format(new Date(zonedMs(day, '12:00', zone)));

  const chartTitle = $derived(
    target.kind === 'at'
      ? t('plan.at.title', { time: tDateTime(new Date(target.atMs)) })
      : target.kind === 'day'
        ? t('plan.day.title', { day: dayText(target.day) })
        : t('plan.chart.title'),
  );
  const chartLabel = $derived(
    t('plan.chart.label', {
      from: tDateTime(new Date(win.fromMs)),
      to: tDateTime(new Date(win.toMs)),
    }),
  );
  const filename = $derived(`plan-${new Date(win.fromMs).toISOString().slice(0, 10)}`);

  // --- controls ---------------------------------------------------------------------------

  let dayInput = $state('');
  let atInput = $state('');
  let compareInput = $state('');
  $effect(() => {
    dayInput = target.kind === 'day' ? target.day : today;
    atInput = localInputValue(target.kind === 'at' ? target.atMs : nowMs - 3_600_000, zone);
    compareInput = localInputValue(compareMs ?? nowMs - 24 * 3_600_000, zone);
  });

  const go = (next: PlanTarget, keepCompare = true): void =>
    router.go(targetHash(next, keepCompare ? compareMs : null));
  const showDay = (day: string): void => {
    if (/^\d{4}-\d{2}-\d{2}$/.test(day) && day <= today) go({ kind: 'day', day });
  };
  const stepDay = (n: number): void =>
    showDay(addDays(target.kind === 'day' ? target.day : today, n));
  const showAt = (): void => {
    const ms = parseLocalInput(atInput, zone);
    if (ms !== null) go({ kind: 'at', atMs: ms });
  };
  const showCompare = (): void => {
    const ms = parseLocalInput(compareInput, zone);
    if (ms !== null) router.go(targetHash(target, ms));
  };
  const live = (): void => router.go('#/plan');

  // --- text alternatives ------------------------------------------------------------------

  const price = (v: number | undefined): string =>
    v == null ? '–' : tNumber(v, { minimumFractionDigits: 2, maximumFractionDigits: 3 });
  const signed = (w: number): string => (w > 0 ? '+' : '') + tPower(w);
  const dash = (v: number | null | undefined, f: (n: number) => string): string =>
    v == null ? '–' : f(v);

  /** One row per full hour. */
  const hourly = $derived(
    intervals.filter((i) => tClock(new Date(i.start)).minute === 0 && i.expected != null),
  );
  const measuredAt = (ms: number) => {
    const a = store.actual;
    const i = a ? actualIndex(a, ms) : -1;
    return a && i >= 0 ? { a, i } : null;
  };

  const spanText = (segs: readonly GanttSeg[]): string =>
    segs.length === 0
      ? t('plan.seg.none')
      : segs
          .map((s) =>
            t('plan.sched.span', {
              from: when(s.fromMs),
              to: when(s.toMs),
              what: t(SEG_LABEL[s.kind]),
            }),
          )
          .join('; ');
  const laneName = (l: GanttLane): string =>
    l.battery ? t('plan.lane.battery') : deviceName(l.id);

  const rows = $derived(store.compare ? compareRows(store.compare, win.fromMs, win.toMs) : []);

  // --- exports ----------------------------------------------------------------------------

  const iso = (ms: number): string => new Date(ms).toISOString();
  const planCsv = $derived(planHistoryApi.atCsv(iso(target.kind === 'at' ? target.atMs : refMs)));
  const actualCsv = $derived(
    planHistoryApi.seriesCsv(
      [...SITE_KEYS, ...deviceIds.slice(0, 3).map(deviceKey)],
      iso(win.fromMs),
      iso(Math.min(win.toMs, nowMs)),
    ),
  );
  const compareCsv = $derived(
    compareMs === null ? null : planHistoryApi.compareCsv(iso(refMs), iso(compareMs)),
  );
  const hasPast = $derived(win.fromMs < nowMs);
</script>

<Card
  title={t('plan.lookback.title')}
  collapsible
  open={target.kind !== 'live' || compareMs !== null}
  density="compact"
  testid="plan-lookback"
>
  <p class="hint">{t('plan.lookback.hint')}</p>
  <div class="fields">
    <div class="field">
      <label for="{uid}-day">{t('plan.lookback.day')}</label>
      <div class="row">
        <button
          type="button"
          class="btn secondary small icon-btn"
          aria-label={t('plan.lookback.day_prev')}
          data-testid="day-prev"
          onclick={() => stepDay(-1)}><Icon name="prev" size={18} /></button
        >
        <input
          id="{uid}-day"
          class="input"
          type="date"
          max={today}
          value={dayInput}
          data-testid="day-input"
          onchange={(e) => showDay(e.currentTarget.value)}
        />
        <button
          type="button"
          class="btn secondary small icon-btn"
          aria-label={t('plan.lookback.day_next')}
          data-testid="day-next"
          disabled={target.kind !== 'day' || target.day >= today}
          onclick={() => stepDay(1)}><Icon name="next" size={18} /></button
        >
      </div>
    </div>
    <div class="field">
      <label for="{uid}-at">{t('plan.lookback.at')}</label>
      <div class="row">
        <input
          id="{uid}-at"
          class="input"
          type="datetime-local"
          bind:value={atInput}
          data-testid="at-input"
        />
        <button type="button" class="btn secondary small" data-testid="at-show" onclick={showAt}
          >{t('plan.lookback.at_show')}</button
        >
      </div>
    </div>
    <div class="field">
      <label for="{uid}-cmp">{t('plan.lookback.compare')}</label>
      <div class="row">
        <input
          id="{uid}-cmp"
          class="input"
          type="datetime-local"
          bind:value={compareInput}
          data-testid="compare-input"
        />
        <button
          type="button"
          class="btn secondary small"
          data-testid="compare-show"
          onclick={showCompare}>{t('plan.lookback.compare_show')}</button
        >
        {#if compareMs !== null}
          <button
            type="button"
            class="btn ghost small"
            data-testid="compare-clear"
            onclick={() => router.go(targetHash(target))}>{t('plan.lookback.compare_clear')}</button
          >
        {/if}
      </div>
    </div>
  </div>
</Card>

<Card title={chartTitle} testid="plan-chart-card">
  {#snippet actions()}
    {#if target.kind !== 'live'}
      <button type="button" class="btn small" data-testid="back-to-live" onclick={live}
        >{t('plan.lookback.live')}</button
      >
    {/if}
  {/snippet}
  <Disclosure summary={t('plan.chart.help')}>
    <p class="hint" id={hintId}>{t('plan.chart.hint')}</p>
  </Disclosure>

  {#if store.snapshot && (target.kind === 'at' || store.snapshot.plan.intervals.length > 0)}
    <p class="hint" data-testid="plan-snapshot">
      {t(target.kind === 'at' ? 'plan.at.created' : 'plan.snapshot.used', {
        time: tDateTime(new Date(store.snapshot.created_at)),
      })}
    </p>
  {/if}
  {#if store.snapshotMissing}
    <p class="notice warn" data-testid="plan-snapshot-missing">{t('plan.snapshot.missing')}</p>
  {:else if store.snapshotError}
    <p class="notice error" role="alert">
      {tDynamic(store.snapshotError, undefined, 'api.error.unknown')}
    </p>
  {/if}
  {#if store.actualError && hasPast}
    <p class="notice error" role="alert" data-testid="plan-actual-error">
      {t('plan.actual.error')}
    </p>
  {/if}
  {#if store.compare}
    <p class="hint" data-testid="plan-compare-summary">
      {t('plan.compare.summary', {
        count: store.compare.changed,
        a: tDateTime(new Date(store.compare.a.created_at)),
        b: tDateTime(new Date(store.compare.b.created_at)),
      })}
    </p>
  {:else if store.compareMissing}
    <p class="notice warn" data-testid="plan-compare-missing">{t('plan.compare.missing')}</p>
  {:else if store.compareError}
    <p class="notice error" role="alert">{t('plan.compare.error')}</p>
  {/if}

  {#if data.start.length === 0}
    <p class="hint" data-testid="plan-window-empty">{t('plan.window.empty')}</p>
  {/if}
  <PlanChart
    {data}
    window={win}
    {lines}
    {nowMs}
    {currency}
    label={chartLabel}
    describedby={hintId}
    actual={measured}
    {lanes}
    compare={compared}
    {marker}
    {deviceName}
    {filename}
  />

  <div class="alternatives">
    <Disclosure summary={t('plan.table.summary')} testid="plan-table">
      <!-- svelte-ignore a11y_no_noninteractive_tabindex (keyboard access to a scrollable table) -->
      <div class="table-scroll" role="region" tabindex="0" aria-label={t('plan.table.caption')}>
        <table class="data-table">
          <caption class="sr-only">{t('plan.table.caption')}</caption>
          <thead>
            <tr>
              <th scope="col">{t('plan.table.time')}</th>
              <th scope="col">{t('plan.table.pv')}</th>
              <th scope="col">{t('plan.table.load')}</th>
              <th scope="col">{t('plan.table.devices')}</th>
              <th scope="col">{t('plan.table.battery')}</th>
              <th scope="col">{t('plan.table.grid')}</th>
              <th scope="col">{t('plan.table.soc')}</th>
              <th scope="col">{t('plan.table.price')}</th>
              {#if store.actual}
                <th scope="col">{t('plan.table.m_pv')}</th>
                <th scope="col">{t('plan.table.m_house')}</th>
                <th scope="col">{t('plan.table.m_grid')}</th>
                <th scope="col">{t('plan.table.m_battery')}</th>
                <th scope="col">{t('plan.table.m_soc')}</th>
              {/if}
            </tr>
          </thead>
          <tbody>
            {#each hourly as i (i.start)}
              {@const e = i.expected!}
              {@const m = measuredAt(Date.parse(i.start))}
              <tr>
                <th scope="row">{tDateTime(new Date(i.start))}</th>
                <td>{tPower(e.pv_p50_w)}</td>
                <td>{tPower(e.load_w)}</td>
                <td>{tPower(e.devices_w ?? 0)}</td>
                <td>{signed(e.battery_w ?? 0)}</td>
                <td>{signed(e.import_w - e.export_w)}</td>
                <td>{e.soc_pct == null ? '–' : tPercent(e.soc_pct)}</td>
                <td>{price(i.prices?.import_price)}</td>
                {#if store.actual}
                  <td>{m ? dash(m.a.pvW[m.i], tPower) : '–'}</td>
                  <td>{m ? dash(m.a.houseW[m.i], tPower) : '–'}</td>
                  <td>{m ? dash(m.a.gridW[m.i], (w) => signed(w)) : '–'}</td>
                  <td>{m ? dash(m.a.batteryW[m.i], (w) => signed(w)) : '–'}</td>
                  <td>{m ? dash(m.a.socPct[m.i], tPercent) : '–'}</td>
                {/if}
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    </Disclosure>

    {#if lanes.length > 0}
      <Disclosure summary={t('plan.sched.summary')} testid="plan-schedule">
        <!-- svelte-ignore a11y_no_noninteractive_tabindex (keyboard access to a scrollable table) -->
        <div class="table-scroll" role="region" tabindex="0" aria-label={t('plan.sched.caption')}>
          <table class="data-table">
            <caption class="sr-only">{t('plan.sched.caption')}</caption>
            <thead>
              <tr>
                <th scope="col">{t('plan.sched.device')}</th>
                <th scope="col">{t('plan.sched.planned')}</th>
                <th scope="col">{t('plan.sched.measured')}</th>
              </tr>
            </thead>
            <tbody>
              {#each lanes as lane (lane.id)}
                <tr data-testid="schedule-row">
                  <th scope="row">{laneName(lane)}</th>
                  <td>{spanText(lane.planned)}</td>
                  <td>{store.actual ? spanText(lane.actual) : '–'}</td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
      </Disclosure>
    {/if}

    {#if store.compare}
      <Disclosure summary={t('plan.compare.table')} open testid="plan-compare-table">
        {#if rows.filter(hasDelta).length === 0}
          <p class="hint" data-testid="plan-compare-none">{t('plan.compare.none')}</p>
        {:else}
          <!-- svelte-ignore a11y_no_noninteractive_tabindex (keyboard access to a scrollable table) -->
          <div class="table-scroll" role="region" tabindex="0" aria-label={t('plan.compare.table')}>
            <table class="data-table">
              <caption class="sr-only">{t('plan.compare.table')}</caption>
              <thead>
                <tr>
                  <th scope="col">{t('plan.table.time')}</th>
                  <th scope="col">{t('plan.compare.col.battery')}</th>
                  <th scope="col">{t('plan.compare.col.soc')}</th>
                  <th scope="col">{t('plan.compare.col.import')}</th>
                  <th scope="col">{t('plan.compare.col.export')}</th>
                  <th scope="col">{t('plan.compare.col.devices')}</th>
                  <th scope="col">{t('plan.compare.col.price')}</th>
                  <th scope="col">{t('plan.compare.col.changed')}</th>
                </tr>
              </thead>
              <tbody>
                {#each rows.filter(hasDelta) as d (d.start)}
                  <tr data-testid="compare-row">
                    <th scope="row">{tDateTime(new Date(d.start))}</th>
                    {#if d.only_in}
                      <td colspan="7"
                        >{t(d.only_in === 'a' ? 'plan.compare.only_a' : 'plan.compare.only_b')}</td
                      >
                    {:else}
                      <td>{dash(d.battery_w_delta, signed)}</td>
                      <td>{dash(d.soc_pct_delta, (v) => (v > 0 ? '+' : '') + tPercent(v))}</td>
                      <td>{dash(d.import_w_delta, signed)}</td>
                      <td>{dash(d.export_w_delta, signed)}</td>
                      <td>{dash(d.devices_w_delta, signed)}</td>
                      <td>{dash(d.import_price_delta, (v) => (v > 0 ? '+' : '') + price(v))}</td>
                      <td>{d.devices_changed.map(deviceName).join(', ') || '–'}</td>
                    {/if}
                  </tr>
                {/each}
              </tbody>
            </table>
          </div>
        {/if}
      </Disclosure>
    {/if}
  </div>
  {#snippet footer()}
    <a class="btn secondary small" href={planCsv} download data-testid="csv-plan"
      ><Icon name="download" size={16} />{t('plan.export.plan_csv')}</a
    >
    {#if hasPast}
      <a class="btn secondary small" href={actualCsv} download data-testid="csv-actual"
        ><Icon name="download" size={16} />{t('plan.export.actual_csv')}</a
      >
    {/if}
    {#if compareCsv}
      <a class="btn secondary small" href={compareCsv} download data-testid="csv-compare"
        ><Icon name="download" size={16} />{t('plan.export.compare_csv')}</a
      >
    {/if}
  {/snippet}
</Card>

<style>
  .fields {
    display: flex;
    flex-wrap: wrap;
    gap: var(--sc-space-3) var(--sc-space-5);
  }
  .field {
    display: grid;
    gap: var(--sc-space-1);
    min-width: 0;
  }
  .row {
    display: flex;
    flex-wrap: wrap;
    gap: var(--sc-space-2);
    align-items: center;
  }
  label {
    font-weight: var(--sc-weight-strong);
    font-size: var(--sc-font-sm);
  }
  .input {
    width: auto;
    min-width: 0;
  }
  .icon-btn {
    min-width: var(--sc-target);
    padding: 0;
    justify-content: center;
  }
  .alternatives {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    border-top: 1px solid var(--sc-outline);
    padding-top: var(--sc-space-2);
  }
  .alternatives :global(.data-table) {
    min-width: 36rem;
  }
</style>
