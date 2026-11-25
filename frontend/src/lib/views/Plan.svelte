<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import { onMount } from 'svelte';
  import { router } from '../router.svelte';
  import { parseTarget, targetHash, todayOf } from '../plan/target';
  import ForecastPanel from '../components/ForecastPanel.svelte';
  import PlannedActions from '../components/PlannedActions.svelte';
  import ReasonList from '../components/ReasonList.svelte';
  import Button from '../ui/Button.svelte';
  import Card from '../ui/Card.svelte';
  import Chip from '../ui/Chip.svelte';
  import Disclosure from '../ui/Disclosure.svelte';
  import EmptyState from '../ui/EmptyState.svelte';
  import Loading from '../ui/Loading.svelte';
  import ToggleGroup from '../ui/ToggleGroup.svelte';
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
  type Range = '24' | '48' | 'day' | 'none';
  const RANGES = $derived([
    { value: '24' as Range, label: t('plan.range.24') },
    { value: '48' as Range, label: t('plan.range.48') },
    { value: 'day' as Range, label: t('plan.range.today') },
  ]);

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
  /** The pressed range: none while the chart shows a past time or another day. */
  const rangeValue = $derived<Range>(
    isLive ? (hours === 24 ? '24' : '48') : dayIsToday ? 'day' : 'none',
  );
  function onRange(v: Range): void {
    if (v === '24') pick(24);
    else if (v === '48') pick(48);
    else if (v === 'day') router.go(todayHash);
  }
</script>

<section class="plan" aria-labelledby="plan-title" data-testid="plan-view">
  <header class="head">
    <div class="title-row">
      <h2 id="plan-title">{t('plan.title')}</h2>
      {#if view}
        <ul class="badges" aria-label={t('plan.status.title')}>
          <li data-testid="plan-source" data-source={source}>
            <Chip tone={source === 'solver' ? 'accent' : 'neutral'}
              >{t(BADGE[source] ?? 'plan.badge.default')}</Chip
            >
          </li>
          {#if view.stale || view.expired}
            <li data-testid="plan-stale"><Chip tone="warn">{t('plan.badge.stale')}</Chip></li>
          {/if}
          <li data-testid="plan-in-use">
            <Chip tone={view.in_use ? 'ok' : 'warn'}
              >{t(view.in_use ? 'plan.badge.in_use' : 'plan.badge.not_in_use')}</Chip
            >
          </li>
        </ul>
      {/if}
    </div>
    <p class="hint intro">{t('plan.intro')}</p>
  </header>

  {#if plan.unavailable}
    <Card testid="plan-unavailable">
      <EmptyState icon="plan" title={t('plan.unavailable')}>
        {#snippet actions()}
          <Button href="#/setup">{t('plan.unavailable.open')}</Button>
        {/snippet}
      </EmptyState>
    </Card>
  {:else if plan.error}
    <p class="notice error" role="alert">{t('plan.load_error')}</p>
  {:else if !plan.loaded}
    <Loading />
  {:else}
    {#if view?.fallback}
      <p class="notice warn" data-testid="plan-fallback">
        {tDynamic(view.fallback.key, view.fallback.args)}
      </p>
    {/if}

    <!-- One toolbar: the time range, the replan button and what the planner did last. -->
    <Card density="compact" testid="plan-toolbar">
      <div class="toolbar">
        <ToggleGroup
          label={t('plan.range.label')}
          options={RANGES}
          value={rangeValue}
          testid="range"
          onselect={onRange}
        />
        <Button
          variant="secondary"
          icon="refresh"
          busy={plan.replanState === 'busy'}
          testid="replan"
          onclick={() => void plan.replan()}
        >
          {plan.replanState === 'busy' ? t('plan.replan.busy') : t('plan.replan')}
        </Button>
      </div>
      <p class="feedback" role="status" aria-live="polite" data-testid="replan-feedback">
        {#if plan.replanState === 'accepted'}
          {t('plan.replan.accepted')}
        {:else if plan.replanState === 'limited' && plan.replanAck}
          {t('plan.replan.limited', { time: tTime(new Date(plan.replanAck.earliest_start_at)) })}
        {:else if plan.replanState === 'error'}
          {t('plan.replan.error')}
        {/if}
      </p>
      {#if status}
        <div class="status" data-testid="plan-status">
          <h3 class="sr-only">{t('plan.status.title')}</h3>
          <ul class="runinfo">
            {#if view?.plan.created_at}
              <li>
                {t('plan.status.created', { time: tDateTime(new Date(view.plan.created_at)) })}
              </li>
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
              <li class="bad">
                {t('plan.status.failures', { count: status.consecutive_failures })}
              </li>
            {/if}
          </ul>
          {#if status.reason}
            <p class="hint">{tDynamic(status.reason.key, status.reason.args)}</p>
          {/if}
          {#if view && view.plan.reasons.length > 0}
            <Disclosure summary={t('plan.status.why')}>
              <ReasonList reasons={view.plan.reasons} />
            </Disclosure>
          {/if}
        </div>
      {/if}
    </Card>

    {#if !hasIntervals(view)}
      <Card testid="plan-empty">
        <EmptyState icon="plan" title={t('plan.empty')} />
      </Card>
    {:else}
      {#await import('../components/PlanChartPanel.svelte')}
        <Loading />
      {:then mod}
        {@const Panel = mod.default}
        <Panel {view} {target} nowMs={plan.nowMs} {currency} {hintId} />
      {:catch}
        <p class="notice error" role="alert">{t('plan.chart.error')}</p>
      {/await}
    {/if}

    <div class="pair">
      {#if hasIntervals(view)}
        <Card title={t('plan.next.title')} testid="plan-next">
          <p class="hint">{t('plan.next.intro')}</p>
          <PlannedActions {actions} />
        </Card>
      {/if}
      <ForecastPanel
        accuracy={plan.accuracy}
        pv={plan.pv}
        openMeteo={plan.usesOpenMeteo}
        failed={plan.accuracy === null && plan.pv === null}
      />
    </div>
  {/if}
</section>

<style>
  .plan {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    gap: var(--sc-space-5);
    min-width: 0;
  }
  .head {
    display: grid;
    gap: var(--sc-space-2);
  }
  .title-row {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--sc-space-2) var(--sc-space-3);
  }
  .intro {
    max-width: 48rem;
  }
  .badges {
    display: flex;
    flex-wrap: wrap;
    gap: var(--sc-space-2);
    list-style: none;
    margin: 0;
    padding: 0;
  }
  .toolbar {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: var(--sc-space-3);
  }
  .feedback {
    margin: 0;
    color: var(--sc-text-muted);
    font-size: var(--sc-font-sm);
  }
  .feedback:empty {
    display: none;
  }
  .status {
    display: grid;
    gap: var(--sc-space-1);
    padding-top: var(--sc-space-3);
    border-top: 1px solid var(--sc-outline);
  }
  .runinfo {
    display: flex;
    flex-wrap: wrap;
    gap: var(--sc-space-1) var(--sc-space-4);
    margin: 0;
    padding: 0;
    list-style: none;
    font-size: var(--sc-font-sm);
    color: var(--sc-text-muted);
  }
  .bad {
    color: var(--sc-error);
    font-weight: var(--sc-weight-strong);
  }
  .status :global(.disclosure summary) {
    font-size: var(--sc-font-sm);
  }
  .pair {
    display: grid;
    gap: var(--sc-space-4);
    grid-template-columns: repeat(auto-fit, minmax(min(100%, 26rem), 1fr));
    align-items: start;
  }
</style>
