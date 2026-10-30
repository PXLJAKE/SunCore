<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import { onMount } from 'svelte';
  import { t, tNumber, tPercent, type MessageKey } from '../i18n/index.svelte';
  import { buildChartData, hasIntervals, planTotals, windowIntervals } from '../plan/model';
  import { app } from '../stores/app.svelte';
  import { live } from '../stores/live.svelte';
  import { plan } from '../stores/plan.svelte';

  const uid = $props.id();
  const BADGE: Record<string, MessageKey> = {
    solver: 'plan.badge.solver',
    heuristic: 'plan.badge.heuristic',
    default: 'plan.badge.default',
  };

  onMount(() => void plan.refresh());
  // The planner reruns after the settings changed; the new plan arrives on the live feed.
  $effect(() => {
    void live.planUpdates;
    void live.snapshotCount;
    plan.syncWithFeed();
  });

  const intervals = $derived(windowIntervals(plan.view, 24, new Date(plan.nowMs)));
  const totals = $derived(planTotals(intervals));
  const data = $derived(buildChartData(intervals));
  const ready = $derived(hasIntervals(plan.view));
  const currency = $derived(app.region?.currency ?? '');
  const kwh = (v: number): string =>
    t('unit.kwh', { value: tNumber(v, { maximumFractionDigits: 1 }) });

  // A small, decorative sparkline of solar power and the planned SoC; the numbers say the same.
  const W = 320;
  const H = 70;
  function path(values: readonly (number | null)[], max: number): string {
    const n = values.length;
    if (n < 2 || max <= 0) return '';
    return values
      .map(
        (v, i) =>
          `${i === 0 ? 'M' : 'L'}${((i / (n - 1)) * W).toFixed(1)},${(H - ((v ?? 0) / max) * (H - 4) - 2).toFixed(1)}`,
      )
      .join('');
  }
  const pvPath = $derived(path(data.pvP50, Math.max(1, ...data.pvP50)));
  const socPath = $derived(path(data.socPct, 100));
</script>

<section class="card stack" aria-labelledby="{uid}-t" data-testid="plan-preview">
  <h3 id="{uid}-t">{t('summary.plan_title')}</h3>
  {#if plan.unavailable}
    <p class="hint" data-testid="plan-preview-none">{t('plan.unavailable')}</p>
  {:else if !ready}
    <p class="hint" role="status" data-testid="plan-preview-none">
      {plan.loaded ? t('summary.plan_none') : t('summary.plan_wait')}
    </p>
  {:else}
    <p>
      <span class="chip-tag" data-testid="plan-preview-source"
        >{t(BADGE[plan.view?.plan.source ?? 'default'] ?? 'plan.badge.default')}</span
      >
    </p>
    <svg viewBox="0 0 {W} {H}" class="spark" aria-hidden="true" preserveAspectRatio="none">
      <path d={pvPath} class="pv" />
      <path d={socPath} class="soc" />
    </svg>
    <p data-testid="plan-preview-numbers">
      {t('summary.plan_numbers', {
        hours: Math.round(totals.hours),
        pv: kwh(totals.pvKwh),
        load: kwh(totals.loadKwh),
        imported: kwh(totals.importKwh),
        exported: kwh(totals.exportKwh),
      })}
    </p>
    {#if totals.endSocPct != null && totals.minSocPct != null && totals.maxSocPct != null}
      <p data-testid="plan-preview-soc">
        {t('summary.plan_soc', {
          from: data.socPct[0] == null ? '–' : tPercent(data.socPct[0]),
          min: tPercent(totals.minSocPct),
          max: tPercent(totals.maxSocPct),
          end: tPercent(totals.endSocPct),
        })}
      </p>
    {/if}
    {#if totals.netCost != null}
      <p class="hint">
        {t('summary.plan_cost', {
          cost: tNumber(totals.netCost, { minimumFractionDigits: 2, maximumFractionDigits: 2 }),
          currency,
        })}
      </p>
    {/if}
  {/if}
  <div class="row">
    <button type="button" class="btn secondary" onclick={() => void plan.refresh()}
      >{t('summary.plan_refresh')}</button
    >
    <a class="btn secondary" href="#/plan">{t('summary.plan_open')}</a>
  </div>
</section>

<style>
  .spark {
    width: 100%;
    height: 70px;
    border-radius: 8px;
    background: var(--secondary-background-color);
  }
  .pv {
    fill: none;
    stroke: var(--energy-solar-color);
    stroke-width: 2;
    vector-effect: non-scaling-stroke;
  }
  .soc {
    fill: none;
    stroke: var(--energy-battery-in-color);
    stroke-width: 2;
    vector-effect: non-scaling-stroke;
  }
  .row {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
  }
  p {
    margin: 0;
  }
</style>
