<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import type { Decision } from '../api/types';
  import { batteryParts, describeAction } from '../control/actions';
  import { t, tDateTime, tDynamic, tPower, tTime, type MessageKey } from '../i18n/index.svelte';

  interface Props {
    decisions: readonly Decision[];
    limit?: number;
    /** Device id -> name; unknown ids are shown as they are. */
    names?: ReadonlyMap<string, string>;
    /** Date and time instead of the time only (the full log). */
    withDate?: boolean;
  }
  let { decisions, limit = 5, names, withDate = false }: Props = $props();

  const KIND: Record<Decision['kind'], MessageKey> = {
    accepted: 'decision.kind.accepted',
    rejected: 'decision.kind.rejected',
    shadow: 'decision.kind.shadow',
    proposed: 'decision.kind.proposed',
    held: 'decision.kind.held',
    failed: 'decision.kind.failed',
  };

  const shown = $derived(decisions.slice(0, limit));
</script>

{#if shown.length === 0}
  <p class="empty">{t('decisions.empty')}</p>
{:else}
  <ul class="decisions">
    {#each shown as d (d.intent.id + d.ts + d.kind)}
      <li>
        <span class="meta">
          <time datetime={d.ts}>{withDate ? tDateTime(new Date(d.ts)) : tTime(new Date(d.ts))}</time
          >
          <span class="asset">{names?.get(d.intent.asset_id) ?? d.intent.asset_id}</span>
          <span class="kind {d.kind}">{t(KIND[d.kind])}</span>
          {#if d.intent.action}
            {@const a = describeAction(d.intent.action, tPower)}
            <span class="action">{t(a.key, a.args)}</span>
            {#if d.intent.action.kind === 'battery'}
              {#each batteryParts(d.intent.action, tPower) as p, i (i)}
                <span class="action">{t(p.key, p.args)}</span>
              {/each}
            {/if}
          {/if}
        </span>
        <span class="reasons">
          {#each d.reasons as r, i (i)}
            <span class="reason">{tDynamic(r.key, r.args)}</span>
          {/each}
        </span>
      </li>
    {/each}
  </ul>
{/if}

<style>
  .decisions {
    list-style: none;
    margin: 0;
    padding: 0;
    display: grid;
    gap: 10px;
  }
  li {
    display: grid;
    gap: 2px;
  }
  .meta {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    color: var(--secondary-text-color);
    font-size: 0.875rem;
  }
  .kind {
    font-weight: 600;
  }
  .kind.rejected,
  .kind.failed {
    color: var(--error-color);
  }
  .action {
    color: var(--primary-text-color);
  }
  .reason {
    display: block;
  }
  .empty {
    margin: 0;
    color: var(--secondary-text-color);
  }
</style>
