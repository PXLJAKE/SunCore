<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import { planApi } from '../api/client';
  import { describeError } from '../api/errors';
  import type { PlanStatus, PlanView } from '../api/types';
  import { t, tDateTime, tDynamic, tNumber } from '../i18n/index.svelte';
  import { live } from '../stores/live.svelte';

  const uid = $props.id();

  let view = $state.raw<PlanView | null>(null);
  let status = $state.raw<PlanStatus | null>(null);
  let error = $state<string | null>(null);
  let busy = $state(false);
  let copied = $state<'idle' | 'ok' | 'failed'>('idle');

  async function load(): Promise<void> {
    busy = true;
    error = null;
    try {
      [view, status] = await Promise.all([planApi.plan(48), planApi.status()]);
    } catch (e) {
      error = describeError(e).key;
    } finally {
      busy = false;
    }
  }

  const signals = $derived(JSON.stringify({ flows: live.flows, health: live.health }, null, 2));
  const planJson = $derived(view ? JSON.stringify(view, null, 2) : '');

  async function copy(): Promise<void> {
    try {
      await navigator.clipboard.writeText(planJson);
      copied = 'ok';
    } catch {
      copied = 'failed';
    }
  }

  const stats = $derived(view?.plan.stats ?? null);
  const dt = (iso: string | null | undefined): string => (iso ? tDateTime(new Date(iso)) : '–');
</script>

<section class="stack" aria-labelledby="{uid}-t" data-testid="debug-views">
  <h3 id="{uid}-t">{t('expert.debug.title')}</h3>

  <details class="card">
    <summary>{t('expert.debug.signals')}</summary>
    <p class="hint">{t('expert.debug.signals.hint')}</p>
    <!-- svelte-ignore a11y_no_noninteractive_tabindex -->
    <pre
      tabindex="0"
      data-testid="signals-json"
      aria-label={t('expert.debug.signals')}>{signals}</pre>
  </details>

  <div class="card stack">
    <div class="row">
      <button
        type="button"
        class="btn secondary small"
        disabled={busy}
        data-testid="debug-load"
        onclick={() => void load()}
        >{view ? t('expert.debug.reload') : t('expert.debug.load')}</button
      >
      <span class="hint">{t('expert.debug.load.hint')}</span>
    </div>
    {#if error}
      <p class="notice error" role="alert">{tDynamic(error, undefined, 'api.error.unknown')}</p>
    {/if}
  </div>

  {#if view}
    <details class="card" open>
      <summary>{t('expert.debug.plan')}</summary>
      <div class="row">
        <button
          type="button"
          class="btn secondary small"
          data-testid="plan-copy"
          onclick={() => void copy()}>{t('expert.debug.copy')}</button
        >
        <span role="status" class="hint">
          {#if copied === 'ok'}{t('expert.debug.copied')}{:else if copied === 'failed'}{t(
              'expert.debug.copy_failed',
            )}{/if}
        </span>
      </div>
      <!-- svelte-ignore a11y_no_noninteractive_tabindex -->
      <pre tabindex="0" data-testid="plan-json" aria-label={t('expert.debug.plan')}>{planJson}</pre>
    </details>

    <details class="card" open data-testid="solver-stats">
      <summary>{t('expert.debug.solver')}</summary>
      <dl>
        <dt>{t('expert.solver.source')}</dt>
        <dd>{view.plan.source}</dd>
        {#if stats}
          <dt>{t('expert.solver.status')}</dt>
          <dd>{stats.status}</dd>
          <dt>{t('expert.solver.time')}</dt>
          <dd>
            {t('expert.solver.seconds', {
              value: tNumber(stats.solve_time_s, { maximumFractionDigits: 3 }),
            })}
          </dd>
          <dt>{t('expert.solver.vars')}</dt>
          <dd>{tNumber(stats.n_vars ?? 0)}</dd>
          <dt>{t('expert.solver.binaries')}</dt>
          <dd>{tNumber(stats.n_binaries ?? 0)}</dd>
          <dt>{t('expert.solver.gap')}</dt>
          <dd>
            {stats.mip_gap == null ? '–' : tNumber(stats.mip_gap, { maximumFractionDigits: 6 })}
          </dd>
          <dt>{t('expert.solver.objective')}</dt>
          <dd>
            {stats.objective == null ? '–' : tNumber(stats.objective, { maximumFractionDigits: 4 })}
          </dd>
        {/if}
        {#if status}
          <dt>{t('expert.solver.last_run')}</dt>
          <dd>{dt(status.last_finished_at)}</dd>
          <dt>{t('expert.solver.last_outcome')}</dt>
          <dd>{status.last_outcome ?? '–'}</dd>
          <dt>{t('expert.solver.last_trigger')}</dt>
          <dd>{status.last_trigger ?? '–'}</dd>
          <dt>{t('expert.solver.failures')}</dt>
          <dd>{tNumber(status.consecutive_failures)}</dd>
        {/if}
      </dl>
      {#if !stats}<p class="hint">{t('expert.solver.none')}</p>{/if}
    </details>
  {/if}
</section>

<style>
  details.card {
    display: grid;
    gap: 8px;
  }
  summary {
    cursor: pointer;
    font-weight: 600;
    min-height: 32px;
  }
  pre {
    margin: 0;
    max-height: 22rem;
    overflow: auto;
    padding: 10px;
    border-radius: 8px;
    border: 1px solid var(--divider-color);
    font-size: 0.8125rem;
    background: var(--secondary-background-color, transparent);
  }
  dl {
    display: grid;
    grid-template-columns: max-content 1fr;
    gap: 4px 16px;
    margin: 0;
  }
  dt {
    color: var(--secondary-text-color);
  }
  dd {
    margin: 0;
    overflow-wrap: anywhere;
  }
  .row {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px 12px;
  }
</style>
