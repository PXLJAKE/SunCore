<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import { tReason } from '../control/reasonText';
  import { tick } from 'svelte';
  import type { Decision } from '../api/types';
  import { batteryParts, describeAction } from '../control/actions';
  import { t, tDateTime, tPower, type MessageKey } from '../i18n/index.svelte';
  import type { ExplainState } from '../stores/decisionLog.svelte';
  import DecisionWhy from './DecisionWhy.svelte';

  interface Props {
    decisions: readonly Decision[];
    /** Row id of each decision (same index). */
    ids: readonly number[];
    names: ReadonlyMap<string, string>;
    explained: Readonly<Record<number, ExplainState>>;
    /** Row id whose "Why?" is open. */
    open: number | null;
    /** Device ids that have a card on the Now view. */
    cards: ReadonlySet<string>;
    ontoggle: (id: number) => void;
    onretry: (id: number) => void;
  }
  let { decisions, ids, names, explained, open, cards, ontoggle, onretry }: Props = $props();

  const uid = $props.id();
  const KIND: Record<Decision['kind'], MessageKey> = {
    accepted: 'decision.kind.accepted',
    rejected: 'decision.kind.rejected',
    shadow: 'decision.kind.shadow',
    proposed: 'decision.kind.proposed',
    held: 'decision.kind.held',
    failed: 'decision.kind.failed',
  };
  const LAYER: Record<Decision['intent']['origin'], MessageKey> = {
    planner: 'log.layer.planner',
    controller: 'log.layer.controller',
    user: 'log.layer.user',
    safety: 'log.layer.safety',
  };

  const name = (assetId: string): string => names.get(assetId) ?? assetId;
  let buttons = $state<Record<number, HTMLButtonElement | undefined>>({});

  /** Close the panel and give the focus back to the button that opened it. */
  async function close(id: number): Promise<void> {
    ontoggle(id);
    await tick();
    buttons[id]?.focus();
  }

  function lines(d: Decision): string[] {
    const a = d.intent.action;
    const main = describeAction(a, tPower);
    const out = [t(main.key, main.args)];
    if (a.kind === 'battery') for (const p of batteryParts(a, tPower)) out.push(t(p.key, p.args));
    return out;
  }
</script>

<div class="wrap">
  <table data-testid="decision-table">
    <caption class="sr-only">{t('log.table')}</caption>
    <thead>
      <tr>
        <th scope="col">{t('log.col.time')}</th>
        <th scope="col">{t('log.col.device')}</th>
        <th scope="col" class="layer">{t('log.col.layer')}</th>
        <th scope="col">{t('log.col.outcome')}</th>
        <th scope="col">{t('log.col.action')}</th>
        <th scope="col">{t('log.col.reasons')}</th>
        <th scope="col"><span class="sr-only">{t('log.col.why')}</span></th>
      </tr>
    </thead>
    <tbody>
      {#each decisions as d, i (ids[i] ?? `${d.intent.id}${d.ts}${d.kind}`)}
        {@const id = ids[i]}
        <tr class:opened={id !== undefined && open === id}>
          <td class="time"><time datetime={d.ts}>{tDateTime(new Date(d.ts))}</time></td>
          <td class="dev">{name(d.intent.asset_id)}</td>
          <td class="layer">{t(LAYER[d.intent.origin])}</td>
          <td class="kind {d.kind}">{t(KIND[d.kind])}</td>
          <td class="act">
            {#each lines(d) as line, k (k)}<span class="line">{line}</span>{/each}
          </td>
          <td class="reasons">
            {#each d.reasons as r, k (k)}<span class="line">{tReason(r)}</span>{/each}
          </td>
          <td class="why-cell">
            {#if id !== undefined}
              <button
                type="button"
                class="btn secondary why-btn"
                bind:this={buttons[id]}
                aria-expanded={open === id}
                aria-controls="{uid}-why-{id}"
                aria-label={t('log.why_for', {
                  device: name(d.intent.asset_id),
                  time: tDateTime(new Date(d.ts)),
                })}
                data-testid="why-button"
                onclick={() => ontoggle(id)}>{t('log.why')}</button
              >
            {/if}
          </td>
        </tr>
        {#if id !== undefined && open === id}
          <tr class="why-row">
            <td colspan="7">
              <DecisionWhy
                id="{uid}-why-{id}"
                state={explained[id] ?? { status: 'loading' }}
                {names}
                hasCard={cards.has(d.intent.asset_id)}
                onretry={() => onretry(id)}
                onclose={() => void close(id)}
              />
            </td>
          </tr>
        {/if}
      {/each}
    </tbody>
  </table>
</div>

<style>
  .wrap {
    overflow-x: auto;
  }
  table {
    width: 100%;
    border-collapse: collapse;
  }
  th {
    text-align: left;
    font-size: 0.875rem;
    color: var(--secondary-text-color);
    font-weight: 600;
  }
  th,
  td {
    padding: 8px 10px 8px 0;
    vertical-align: top;
    border-bottom: 1px solid var(--divider-color);
  }
  .line {
    display: block;
  }
  .kind {
    font-weight: 600;
  }
  .kind.rejected,
  .kind.failed {
    color: var(--error-color);
  }
  .time {
    white-space: nowrap;
  }
  .why-btn {
    white-space: nowrap;
    min-height: 44px;
  }
  tr.opened td {
    border-bottom-color: transparent;
  }
  .why-row td {
    padding-top: 0;
  }

  /* Phone: one card per entry instead of a seven-column table. */
  @media (max-width: 48rem) {
    thead {
      position: absolute;
      width: 1px;
      height: 1px;
      overflow: hidden;
      clip: rect(0, 0, 0, 0);
    }
    table,
    tbody,
    tr,
    td {
      display: block;
    }
    tr:not(.why-row) {
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 2px 12px;
      padding: 10px 0;
      border-bottom: 1px solid var(--divider-color);
    }
    tr.opened {
      border-bottom-color: transparent;
    }
    td {
      border: 0;
      padding: 0;
    }
    .time {
      grid-column: 1;
      color: var(--secondary-text-color);
      font-size: 0.875rem;
    }
    .dev {
      grid-column: 1;
      font-weight: 600;
    }
    .kind {
      grid-column: 2;
      grid-row: 1;
      text-align: right;
    }
    .layer {
      display: none;
    }
    .act,
    .reasons {
      grid-column: 1 / -1;
    }
    .why-cell {
      grid-column: 1 / -1;
      margin-top: 6px;
    }
    .why-row td {
      padding: 0 0 8px;
    }
  }
</style>
