<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  // The kept runs (the last five): open one, take its settings over, delete it or cancel it.
  import type { BacktestRun } from '../api/types';
  import { dayLabel } from '../history/labels';
  import { i18n, t, tDateTime, type MessageKey } from '../i18n/index.svelte';

  interface Props {
    runs: readonly BacktestRun[];
    selectedId: number | null;
    onselect: (id: number) => void;
    onremove: (id: number) => void;
  }
  let { runs, selectedId, onselect, onremove }: Props = $props();

  const STATUS: Record<BacktestRun['status'], MessageKey> = {
    running: 'whatif.status.running',
    done: 'whatif.status.done',
    failed: 'whatif.status.failed',
    cancelled: 'whatif.status.cancelled',
  };
  const locale = $derived(i18n.locale);
</script>

<section class="card stack" aria-labelledby="wi-runs" data-testid="whatif-runs">
  <h3 id="wi-runs">{t('whatif.runs.title')}</h3>
  <p class="hint">{t('whatif.runs.hint')}</p>
  {#if runs.length === 0}
    <p data-testid="whatif-runs-empty">{t('whatif.runs.empty')}</p>
  {:else}
    <ul class="runs">
      {#each runs as run (run.id)}
        {@const range = t('history.range', {
          from: dayLabel(run.start, locale),
          to: dayLabel(run.end, locale),
        })}
        <li
          class="run"
          data-testid="whatif-run"
          data-id={run.id}
          data-status={run.status}
          aria-current={run.id === selectedId ? 'true' : undefined}
        >
          <div class="what">
            <strong>{range}</strong>
            <span class="chip-tag" class:warn={run.status === 'failed'}
              >{t(STATUS[run.status])}</span
            >
            <span class="hint"
              >{t('whatif.runs.started', { when: tDateTime(new Date(run.created_at)) })}</span
            >
          </div>
          <div class="buttons">
            {#if run.status !== 'running'}
              <button
                type="button"
                class="btn secondary small"
                disabled={run.id === selectedId}
                aria-label={t('whatif.change.pair', { name: t('whatif.runs.show'), value: range })}
                onclick={() => onselect(run.id)}>{t('whatif.runs.show')}</button
              >
            {/if}
            <button
              type="button"
              class="btn secondary small"
              data-testid={run.status === 'running' ? 'whatif-run-cancel' : 'whatif-run-delete'}
              aria-label={t('whatif.change.pair', {
                name: t(run.status === 'running' ? 'action.cancel' : 'whatif.runs.delete'),
                value: range,
              })}
              onclick={() => onremove(run.id)}
              >{t(run.status === 'running' ? 'action.cancel' : 'whatif.runs.delete')}</button
            >
          </div>
        </li>
      {/each}
    </ul>
  {/if}
</section>

<style>
  .runs {
    display: grid;
    gap: 8px;
    margin: 0;
    padding: 0;
    list-style: none;
  }
  .run {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 8px 16px;
    padding: 10px 12px;
    border: 1px solid var(--divider-color);
    border-radius: var(--sc-radius);
  }
  .run[aria-current='true'] {
    border-color: var(--primary-color);
    box-shadow: inset 4px 0 0 var(--primary-color);
  }
  .what {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 6px 12px;
    min-width: 0;
  }
  .buttons {
    display: flex;
    gap: 8px;
  }
</style>
