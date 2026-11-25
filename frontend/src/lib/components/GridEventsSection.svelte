<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  // History: grid events (island, §14a, import above the limit, new peaks) and the monthly peaks.
  import { onMount } from 'svelte';
  import { flexApi } from '../api/client';
  import { describeError } from '../api/errors';
  import type { GridEvents } from '../api/types';
  import { eventKey } from '../grid/present';
  import { t, tDateTime, tDynamic, tPower } from '../i18n/index.svelte';
  import Card from '../ui/Card.svelte';

  const DAYS = 30;

  let data = $state.raw<GridEvents | null>(null);
  let error = $state<string | null>(null);

  onMount(() => {
    flexApi.gridEvents(DAYS).then(
      (d) => (data = d),
      (e: unknown) => (error = describeError(e).key),
    );
  });

  const empty = $derived(data !== null && data.events.length === 0 && data.peaks.length === 0);
</script>

<Card title={t('history.grid.title')} testid="history-grid">
  <p class="hint">{t('history.grid.hint', { days: DAYS })}</p>
  {#if error}
    <p class="notice error" role="alert">{tDynamic(error, undefined, 'api.error.unknown')}</p>
  {:else if empty}
    <p class="hint" data-testid="history-grid-empty">{t('history.grid.none')}</p>
  {:else if data}
    {#if data.peaks.length > 0}
      <!-- svelte-ignore a11y_no_noninteractive_tabindex (keyboard access to a scrollable table) -->
      <div class="table-scroll" role="region" tabindex="0" aria-label={t('history.grid.peaks')}>
        <table class="data-table" data-testid="table-peaks">
          <caption class="sr-only">{t('history.grid.peaks')}</caption>
          <thead>
            <tr>
              <th scope="col">{t('history.grid.month')}</th>
              <th scope="col">{t('history.grid.peak')}</th>
              <th scope="col">{t('history.grid.when')}</th>
            </tr>
          </thead>
          <tbody>
            {#each data.peaks as p (p.month)}
              <tr data-testid="peak-row">
                <th scope="row">{p.month}</th>
                <td>{tPower(p.peak_w)}</td>
                <td>{tDateTime(new Date(p.slot))}</td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    {/if}
    {#if data.events.length > 0}
      <!-- svelte-ignore a11y_no_noninteractive_tabindex (keyboard access to a scrollable table) -->
      <div class="table-scroll" role="region" tabindex="0" aria-label={t('history.grid.events')}>
        <table class="data-table" data-testid="table-grid-events">
          <caption class="sr-only">{t('history.grid.events')}</caption>
          <thead>
            <tr>
              <th scope="col">{t('history.grid.when')}</th>
              <th scope="col">{t('history.grid.event')}</th>
              <th scope="col">{t('history.grid.value')}</th>
            </tr>
          </thead>
          <tbody>
            {#each data.events as e (e.ts + e.kind)}
              {@const key = eventKey(e)}
              <tr data-testid="grid-event-row">
                <td>{tDateTime(new Date(e.ts))}</td>
                <th scope="row">{key ? t(key) : e.kind}</th>
                <td>{e.value_w == null ? '–' : tPower(e.value_w)}</td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    {/if}
  {/if}
</Card>
