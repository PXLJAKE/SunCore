<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import { t, tDynamic, tPercent, tPower, tTime } from '../i18n/index.svelte';
  import type { PlannedAction } from '../plan/model';
  import ReasonList from './ReasonList.svelte';

  interface Props {
    actions: readonly PlannedAction[];
    /** Heading level of the list's title is the parent's business; the list has no title. */
    testid?: string;
  }
  let { actions, testid = 'planned-actions' }: Props = $props();

  const socText = (a: PlannedAction): string | null => {
    if (a.socToPct == null) return null;
    return a.socFromPct == null
      ? t('plan.next.soc_to', { to: tPercent(a.socToPct) })
      : t('plan.next.soc', { from: tPercent(a.socFromPct), to: tPercent(a.socToPct) });
  };
</script>

{#if actions.length === 0}
  <p class="hint" data-testid="{testid}-none">{t('plan.next.none')}</p>
{:else}
  <ol class="actions" data-testid={testid}>
    {#each actions as a (a.start.getTime())}
      {@const soc = socText(a)}
      <li data-testid="planned-action">
        <div class="head">
          <time class="when" datetime={a.start.toISOString()}
            >{t('plan.next.range', { from: tTime(a.start), to: tTime(a.end) })}</time
          >
          {#if soc}<span class="chip-tag">{soc}</span>{/if}
          {#if a.dischargeLock}<span class="chip-tag warn">{t('plan.next.lock')}</span>{/if}
          {#if a.gridChargeW != null && a.gridChargeW > 0}
            <span class="chip-tag warn"
              >{t('plan.next.grid_charge', { power: tPower(a.gridChargeW) })}</span
            >
          {/if}
        </div>
        {#if a.headline}
          <p class="what">{tDynamic(a.headline.key, a.headline.args)}</p>
        {/if}
        <details class="why">
          <summary>{t('plan.next.why')}</summary>
          <ReasonList reasons={a.reasons} />
          {#if a.flows}
            <p class="hint">
              {t('plan.next.facts', {
                pv: tPower(a.flows.pv_p50_w),
                load: tPower(a.flows.load_w),
                battery: tPower(a.flows.battery_w ?? 0),
                imported: tPower(a.flows.import_w),
                exported: tPower(a.flows.export_w),
              })}
            </p>
          {/if}
        </details>
      </li>
    {/each}
  </ol>
{/if}

<style>
  .actions {
    display: grid;
    gap: 12px;
    margin: 0;
    padding: 0;
    list-style: none;
  }
  li {
    display: grid;
    gap: 4px;
    padding-top: 12px;
    border-top: 1px solid var(--divider-color);
  }
  li:first-child {
    border-top: 0;
    padding-top: 0;
  }
  .head {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 4px 10px;
  }
  .when {
    font-weight: 700;
    font-variant-numeric: tabular-nums;
  }
  .what {
    margin: 0;
  }
  summary {
    cursor: pointer;
    min-height: 32px;
    display: flex;
    align-items: center;
    font-weight: 600;
    color: var(--primary-color);
  }
  details > :global(*:not(summary)) {
    margin-top: 6px;
  }
</style>
