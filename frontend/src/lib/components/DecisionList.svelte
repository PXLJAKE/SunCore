<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import { tReason } from '../control/reasonText';
  import type { Decision } from '../api/types';
  import { batteryParts, describeAction } from '../control/actions';
  import { splitSharedReasons } from '../control/present';
  import Icon from '../ui/Icon.svelte';
  import { t, tDateTime, tPower, tTime, type MessageKey } from '../i18n/index.svelte';

  interface Props {
    decisions: readonly Decision[];
    limit?: number;
    /** Device id -> name; unknown ids are shown as they are. */
    names?: ReadonlyMap<string, string>;
    /** Date and time instead of the time only (the full log). */
    withDate?: boolean;
  }
  let { decisions, limit = 4, names, withDate = false }: Props = $props();

  const KIND: Record<Decision['kind'], MessageKey> = {
    accepted: 'decision.kind.accepted',
    rejected: 'decision.kind.rejected',
    shadow: 'decision.kind.shadow',
    proposed: 'decision.kind.proposed',
    held: 'decision.kind.held',
    failed: 'decision.kind.failed',
  };

  const shown = $derived(decisions.slice(0, limit));
  // A note every entry carries (shadow mode: nothing switched) is said once, above the list.
  const split = $derived(splitSharedReasons(shown));
</script>

{#if shown.length === 0}
  <p class="empty">{t('decisions.empty')}</p>
{:else}
  {#if split.shared.length > 0}
    <p class="shared" data-testid="decisions-shared">
      <Icon name="info" size={16} />
      <span>{split.shared.map((r) => tReason(r)).join(' ')}</span>
    </p>
  {/if}
  <ul class="decisions">
    {#each shown as d, i (d.intent.id + d.ts + d.kind)}
      <li data-testid="decision-item">
        <time datetime={d.ts}>{withDate ? tDateTime(new Date(d.ts)) : tTime(new Date(d.ts))}</time>
        <span class="body">
          <span class="line">
            <strong class="asset">{names?.get(d.intent.asset_id) ?? d.intent.asset_id}</strong>
            {#if d.intent.action}
              {@const a = describeAction(d.intent.action, tPower)}
              <span>{t(a.key, a.args)}</span>
              {#if d.intent.action.kind === 'battery'}
                {#each batteryParts(d.intent.action, tPower) as p, k (k)}
                  <span>{t(p.key, p.args)}</span>
                {/each}
              {/if}
            {/if}
            <span class="chip-tag kind" class:warn={d.kind === 'rejected' || d.kind === 'failed'}
              >{t(KIND[d.kind])}</span
            >
          </span>
          {#each split.own[i] ?? [] as r, k (k)}
            <span class="reason">{tReason(r)}</span>
          {/each}
        </span>
      </li>
    {/each}
  </ul>
{/if}

<style>
  .shared {
    display: flex;
    align-items: flex-start;
    gap: var(--sc-space-2);
    margin: 0;
    font-size: var(--sc-font-sm);
    color: var(--sc-text-muted);
  }
  .shared :global(.icon) {
    margin-top: 2px;
    color: var(--sc-info);
  }
  .decisions {
    list-style: none;
    margin: 0;
    padding: 0;
    display: grid;
  }
  li {
    display: grid;
    grid-template-columns: 4.5rem minmax(0, 1fr);
    gap: var(--sc-space-3);
    padding: var(--sc-space-2) 0;
    border-top: 1px solid var(--sc-outline);
  }
  li:first-child {
    border-top: 0;
  }
  time {
    font-size: var(--sc-font-sm);
    font-variant-numeric: tabular-nums;
    color: var(--sc-text-muted);
  }
  .body {
    display: grid;
    gap: 2px;
    min-width: 0;
  }
  .line {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--sc-space-1) var(--sc-space-2);
  }
  .kind.warn {
    background: var(--sc-error-bg);
    color: var(--sc-error);
  }
  .reason {
    font-size: var(--sc-font-sm);
    color: var(--sc-text-muted);
  }
  .empty {
    margin: 0;
    color: var(--sc-text-muted);
  }
</style>
