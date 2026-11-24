<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import { onMount } from 'svelte';
  import { router } from '../router.svelte';
  import { parseTarget, targetHash, todayOf } from '../plan/target';
  import ForecastPanel from '../components/ForecastPanel.svelte';
  import PlannedActions from '../components/PlannedActions.svelte';
  import { i18n, t, tDateTime, tDynamic, tTime, type MessageKey } from '../i18n/index.svelte';
  import { hasIntervals, plannedActions, windowIntervals, type HoursWindow } from '../plan/model';
  import { app } from '../stores/app.svelte';
  import { live } from '../stores/live.svelte';
  import { plan } from '../stores/plan.svelte';

  const uid = $props.id();
  const hintId = `${uid}-hint`;
  const BADGE: Record<string, MessageKey> = {
    solver: 'plan.badge.solver',
    heuristic: 'plan.badge.heuristic',
    default: 'plan.badge.default',
  };
  const OUTCOME: Record<string, MessageKey> = {
    ok: 'plan.outcome.ok',
    failed: 'plan.outcome.failed',
    skipped: 'plan.outcome.skipped',
  };
  const TRIGGER: Record<string, MessageKey> = {
    start: 'plan.trigger.start',
    schedule: 'plan.trigger.schedule',
    prices: 'plan.trigger.prices',
    forecast: 'plan.trigger.forecast',
    deviation: 'plan.trigger.deviation',
    config: 'plan.trigger.config',
    override: 'plan.trigger.override',
    manual: 'plan.trigger.manual',
  };

  let hours = $state<HoursWindow>(24);

  onMount(() => {
    void plan.refresh();
    void plan.refreshForecasts();
  });

  // A plan announced on the live feed is fetched once, with the forecasts that go with it.
  $effect(() => {
    void live.planUpdates;
    void live.snapshotCount;
    plan.syncWithFeed(true);
  });

  const view = $derived(plan.view);
  const now = $derived(new Date(plan.nowMs));
  const intervals = $derived(windowIntervals(view, hours, now));
  const actions = $derived(plannedActions(intervals, now, 12));
  const source = $derived(view?.plan.source ?? 'default');
  const currency = $derived(app.region?.currency ?? '');
  const status = $derived(plan.status);

  const zone = $derived(i18n.timeZone ?? Intl.DateTimeFormat().resolvedOptions().timeZone);
  const target = $derived(parseTarget(router.query, hours, zone));
  const isLive = $derived(target.kind === 'live');
  const todayHash = $derived(targetHash({ kind: 'day', day: todayOf(plan.nowMs, zone) }));
  const dayIsToday = $derived(target.kind === 'day' && target.day === todayOf(plan.nowMs, zone));
  function pick(h: HoursWindow): void {
    hours = h;
    if (!isLive) router.go('#/plan');
  }
</script>

<section class="plan" aria-labelledby="plan-title" data-testid="plan-view">
  <header class="head">
    <h2 id="plan-title">{t('plan.title')}</h2>
    {#if view}
      <ul class="badges" aria-label={t('plan.status.title')}>
        <li class="chip-tag" data-testid="plan-source" data-source={source}>
          {t(BADGE[source] ?? 'plan.badge.default')}
        </li>
        {#if view.stale || view.expired}
          <li class="chip-tag warn" data-testid="plan-stale">{t('plan.badge.stale')}</li>
        {/if}
        <li class="chip-tag" class:warn={!view.in_use} data-testid="plan-in-use">
          {t(view.in_use ? 'plan.badge.in_use' : 'plan.badge.not_in_use')}
        </li>
      </ul>
    {/if}
  </header>
  <p class="hint">{t('plan.intro')}</p>

  {#if plan.unavailable}
    <div class="card" data-testid="plan-unavailable">
      <p>{t('plan.unavailable')}</p>
      <a class="btn" href="#/setup">{t('plan.unavailable.open')}</a>
    </div>
  {:else if plan.error}
    <p class="notice error" role="alert">{t('plan.load_error')}</p>
  {:else if !plan.loaded}
    <p class="hint" role="status">{t('wizard.loading')}</p>
  {:else}
    {#if view?.fallback}
      <p class="notice warn" data-testid="plan-fallback">
        {tDynamic(view.fallback.key, view.fallback.args)}
      </p>
    {/if}

    <div class="card controls">
      <div class="range" role="group" aria-label={t('plan.range.label')}>
        {#each [24, 48] as const as h (h)}
          <button
            type="button"
            class="seg-btn"
            aria-pressed={isLive && hours === h}
            data-testid="range-{h}"
            onclick={() => pick(h)}>{t(h === 24 ? 'plan.range.24' : 'plan.range.48')}</button
          >
        {/each}
        <button
          type="button"
          class="seg-btn"
          aria-pressed={dayIsToday}
          data-testid="range-day"
          onclick={() => router.go(todayHash)}>{t('plan.range.today')}</button
        >
      </div>
      <button
        type="button"
        class="btn"
        disabled={plan.replanState === 'busy'}
        data-testid="replan"
        onclick={() => void plan.replan()}
      >
        {plan.replanState === 'busy' ? t('plan.replan.busy') : t('plan.replan')}
      </button>
      <p class="feedback" role="status" aria-live="polite" data-testid="replan-feedback">
        {#if plan.replanState === 'accepted'}
          {t('plan.replan.accepted')}
        {:else if plan.replanState === 'limited' && plan.replanAck}
          {t('plan.replan.limited', { time: tTime(new Date(plan.replanAck.earliest_start_at)) })}
        {:else if plan.replanState === 'error'}
          {t('plan.replan.error')}
        {/if}
      </p>
    </div>

    {#if status}
      <div class="card status stack" data-testid="plan-status">
        <h3>{t('plan.status.title')}</h3>
        <ul class="facts">
          {#if view?.plan.created_at}
            <li>{t('plan.status.created', { time: tDateTime(new Date(view.plan.created_at)) })}</li>
          {/if}
          {#if status.last_started_at && status.last_outcome}
            <li data-testid="plan-last-run">
              {t('plan.status.last_run', {
                time: tDateTime(new Date(status.last_started_at)),
                outcome: t(OUTCOME[status.last_outcome] ?? 'plan.outcome.ok'),
                seconds: status.last_duration_s ?? 0,
                trigger: status.last_trigger
                  ? t(TRIGGER[status.last_trigger] ?? 'plan.trigger.manual')
                  : '–',
              })}
            </li>
          {:else}
            <li>{t('plan.status.never')}</li>
          {/if}
          {#if status.running}<li>{t('plan.status.running')}</li>{/if}
          {#if status.next_scheduled_at}
            <li data-testid="plan-next-run">
              {t('plan.status.next_run', { time: tDateTime(new Date(status.next_scheduled_at)) })}
            </li>
          {/if}
          {#if status.consecutive_failures > 0}
            <li class="bad">{t('plan.status.failures', { count: status.consecutive_failures })}</li>
          {/if}
        </ul>
        {#if status.reason}
          <p class="hint">{tDynamic(status.reason.key, status.reason.args)}</p>
        {/if}
        {#if view && view.plan.reasons.length > 0}
          <details>
            <summary>{t('plan.next.why')}</summary>
            <ul class="reasons">
              {#each view.plan.reasons as r, i (i)}
                <li>{tDynamic(r.key, r.args)}</li>
              {/each}
            </ul>
          </details>
        {/if}
      </div>
    {/if}

    {#if !hasIntervals(view)}
      <p class="card hint" data-testid="plan-empty">{t('plan.empty')}</p>
    {:else}
      {#await import('../components/PlanChartPanel.svelte')}
        <p class="hint" role="status">{t('plan.chart.loading')}</p>
      {:then mod}
        {@const Panel = mod.default}
        <Panel {view} {target} nowMs={plan.nowMs} {currency} {hintId} />
      {:catch}
        <p class="notice error" role="alert">{t('plan.chart.error')}</p>
      {/await}

      <div class="card stack" aria-labelledby="{uid}-next">
        <h3 id="{uid}-next">{t('plan.next.title')}</h3>
        <p class="hint">{t('plan.next.intro')}</p>
        <PlannedActions {actions} />
      </div>
    {/if}

    <ForecastPanel
      accuracy={plan.accuracy}
      pv={plan.pv}
      openMeteo={plan.usesOpenMeteo}
      failed={plan.accuracy === null && plan.pv === null}
    />
  {/if}
</section>

<style>
  .plan {
    display: grid;
    gap: var(--sc-gap);
  }
  .head {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px 16px;
  }
  .badges {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    list-style: none;
    margin: 0;
    padding: 0;
  }
  .controls {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 12px 16px;
  }
  .range {
    display: flex;
    border: 1px solid var(--primary-color);
    border-radius: 22px;
    overflow: hidden;
  }
  .seg-btn {
    min-width: 72px;
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
  .feedback {
    flex: 1 1 14rem;
    margin: 0;
    min-height: 1.45em;
    color: var(--secondary-text-color);
    font-size: 0.875rem;
  }
  .facts,
  .reasons {
    margin: 0;
    padding-left: 20px;
  }
  .facts {
    padding-left: 0;
    list-style: none;
    display: grid;
    gap: 4px;
  }
  .bad {
    color: var(--error-color);
    font-weight: 600;
  }
  summary {
    cursor: pointer;
    min-height: 32px;
    display: flex;
    align-items: center;
    font-weight: 600;
    color: var(--primary-color);
  }
</style>
