<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import { tReason } from '../control/reasonText';
  import { t, tPercent, tPower, tTime } from '../i18n/index.svelte';
  import type { PlannedAction } from '../plan/model';
  import Icon from '../ui/Icon.svelte';
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
  <!-- A compact timeline: time, what happens, chips; the row opens to show why. -->
  <ol class="timeline" data-testid={testid}>
    {#each actions as a (a.start.getTime())}
      {@const soc = socText(a)}
      <li data-testid="planned-action">
        <details>
          <summary>
            <time class="when" datetime={a.start.toISOString()}
              >{t('plan.next.range', { from: tTime(a.start), to: tTime(a.end) })}</time
            >
            <span class="what">
              {#if a.headline}<span>{tReason(a.headline)}</span>{/if}
              {#if soc}<span class="chip-tag">{soc}</span>{/if}
              {#if a.dischargeLock}<span class="chip-tag warn">{t('plan.next.lock')}</span>{/if}
              {#if a.gridChargeW != null && a.gridChargeW > 0}
                <span class="chip-tag warn"
                  >{t('plan.next.grid_charge', { power: tPower(a.gridChargeW) })}</span
                >
              {/if}
            </span>
            <span class="why">{t('plan.next.why')}<Icon name="chevron" size={16} /></span>
          </summary>
          <div class="detail">
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
          </div>
        </details>
      </li>
    {/each}
  </ol>
{/if}

<style>
  .timeline {
    display: grid;
    margin: 0;
    padding: 0;
    list-style: none;
  }
  li {
    position: relative;
    padding-inline-start: var(--sc-space-5);
  }
  /* The rail with a dot per action. */
  li::before {
    content: '';
    position: absolute;
    inset-inline-start: 5px;
    top: 0;
    bottom: 0;
    width: 2px;
    background: var(--sc-outline);
  }
  li:first-child::before {
    top: 20px;
  }
  li:last-child::before {
    bottom: calc(100% - 20px);
  }
  li::after {
    content: '';
    position: absolute;
    inset-inline-start: 1px;
    top: 15px;
    width: 10px;
    height: 10px;
    border-radius: 50%;
    background: var(--sc-surface);
    box-shadow: inset 0 0 0 2px var(--sc-accent);
  }
  summary {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    gap: 2px var(--sc-space-3);
    align-items: center;
    min-height: var(--sc-target);
    padding: var(--sc-space-1) 0;
    cursor: pointer;
    list-style: none;
  }
  summary::-webkit-details-marker {
    display: none;
  }
  .when {
    grid-column: 1;
    font-size: var(--sc-font-sm);
    font-weight: var(--sc-weight-strong);
    font-variant-numeric: tabular-nums;
    color: var(--sc-text-muted);
  }
  .what {
    grid-column: 1 / -1;
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--sc-space-1) var(--sc-space-2);
    min-width: 0;
  }
  .why {
    grid-column: 2;
    grid-row: 1;
    display: inline-flex;
    align-items: center;
    gap: 2px;
    font-size: var(--sc-font-sm);
    font-weight: var(--sc-weight-strong);
    color: var(--sc-accent);
  }
  .why :global(.icon) {
    transition: transform var(--sc-motion-fast) ease;
  }
  details[open] .why :global(.icon) {
    transform: rotate(180deg);
  }
  .detail {
    display: grid;
    gap: var(--sc-space-2);
    margin: 0 0 var(--sc-space-2);
    padding: var(--sc-space-3);
    border-radius: var(--sc-radius-md);
    background: var(--sc-surface-sunken);
    font-size: var(--sc-font-sm);
  }
</style>
