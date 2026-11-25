<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import { tReason } from '../control/reasonText';
  import { tick } from 'svelte';
  import type { Decision, Reason } from '../api/types';
  import Icon from '../ui/Icon.svelte';
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
    /**
     * Reasons to show per decision (same index), e.g. without a note every entry shares and the
     * caller shows once; default: all reasons of each decision.
     */
    reasons?: readonly (readonly Reason[])[];
    /** Device ids that have a card on the Now view. */
    cards: ReadonlySet<string>;
    ontoggle: (id: number) => void;
    onretry: (id: number) => void;
  }
  let { decisions, ids, reasons, names, explained, open, cards, ontoggle, onretry }: Props =
    $props();

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
          <td class="kind"><span class="tag {d.kind}">{t(KIND[d.kind])}</span></td>
          <td class="act">
            {#each lines(d) as line, k (k)}<span class="line">{line}</span>{/each}
          </td>
          <td class="reasons">
            {#each reasons?.[i] ?? d.reasons as r, k (k)}<span class="line">{tReason(r)}</span
              >{/each}
          </td>
          <td class="why-cell">
            {#if id !== undefined}
              <button
                type="button"
                class="btn ghost small why-btn"
                bind:this={buttons[id]}
                aria-expanded={open === id}
                aria-controls="{uid}-why-{id}"
                aria-label={t('log.why_for', {
                  device: name(d.intent.asset_id),
                  time: tDateTime(new Date(d.ts)),
                })}
                data-testid="why-button"
                onclick={() => ontoggle(id)}
                ><span class="chev" class:up={open === id}><Icon name="chevron" size={16} /></span
                >{t('log.why')}</button
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
    font-size: var(--sc-font-sm);
  }
  th {
    text-align: start;
    font-size: var(--sc-font-xs);
    color: var(--sc-text-muted);
    font-weight: var(--sc-weight-strong);
  }
  th,
  td {
    padding: var(--sc-space-2) var(--sc-space-3) var(--sc-space-2) 0;
    vertical-align: top;
    border-bottom: 1px solid var(--sc-outline);
  }
  .line {
    display: block;
  }
  .reasons {
    color: var(--sc-text-muted);
  }
  .dev {
    font-weight: var(--sc-weight-strong);
  }
  .tag {
    display: inline-flex;
    align-items: center;
    min-height: 22px;
    padding: 0 var(--sc-space-2);
    border-radius: var(--sc-radius-pill);
    background: var(--sc-surface-sunken);
    color: var(--sc-text-muted);
    font-size: var(--sc-font-xs);
    font-weight: var(--sc-weight-strong);
    white-space: nowrap;
  }
  .tag.accepted {
    background: var(--sc-ok-bg);
    color: var(--sc-ok);
  }
  .tag.proposed,
  .tag.held {
    background: var(--sc-warn-bg);
    color: var(--sc-warn);
  }
  .tag.rejected,
  .tag.failed {
    background: var(--sc-error-bg);
    color: var(--sc-error);
  }
  .time {
    white-space: nowrap;
    color: var(--sc-text-muted);
    font-variant-numeric: tabular-nums;
  }
  .why-btn {
    white-space: nowrap;
    min-height: var(--sc-target);
    gap: var(--sc-space-1);
  }
  .chev {
    display: inline-flex;
    transform: rotate(-90deg);
    transition: transform var(--sc-motion-fast) ease;
  }
  .chev.up {
    transform: none;
  }
  tr.opened td {
    border-bottom-color: transparent;
  }
  .why-row td {
    padding-top: 0;
  }

  /* Phone: one compact entry per decision instead of a seven-column table. */
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
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 2px var(--sc-space-3);
      padding: var(--sc-space-3) 0;
      border-bottom: 1px solid var(--sc-outline);
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
      grid-row: 1;
      font-size: var(--sc-font-xs);
    }
    .kind {
      grid-column: 2;
      grid-row: 1;
      text-align: end;
    }
    .dev {
      grid-column: 1 / -1;
      font-size: var(--sc-font-md);
    }
    .layer {
      display: none;
    }
    .act,
    .reasons {
      grid-column: 1;
    }
    .why-cell {
      grid-column: 2;
      grid-row: 3 / span 2;
      align-self: end;
    }
    .why-row td {
      padding: 0 0 var(--sc-space-2);
    }
  }
</style>
