<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  // One chart card of the history view: title, hint, the chart, the CSV link and the data as a
  // table (the text alternative). The section decides nothing about the data.
  import type { Snippet } from 'svelte';
  import { tDynamic } from '../i18n/index.svelte';

  interface Props {
    id: string;
    title: string;
    hint: string;
    /** Range or total shown under the hint. */
    caption?: string;
    csvHref: string;
    csvLabel: string;
    tableSummary: string;
    /** Catalogue key of a failed load. */
    error?: string | null;
    /** Text instead of the chart when there is nothing to draw. */
    empty?: string | null;
    chart: Snippet;
    table: Snippet;
  }
  let {
    id,
    title,
    hint,
    caption,
    csvHref,
    csvLabel,
    tableSummary,
    error = null,
    empty = null,
    chart,
    table,
  }: Props = $props();
  const uid = $props.id();
</script>

<section class="card stack" aria-labelledby="{uid}-t" data-testid="history-{id}">
  <h3 id="{uid}-t">{title}</h3>
  <p class="hint">{hint}</p>
  {#if caption}<p class="hint" data-testid="history-{id}-caption">{caption}</p>{/if}
  {#if error}
    <p class="notice error" role="alert">{tDynamic(error, undefined, 'api.error.unknown')}</p>
  {:else if empty}
    <p class="hint" data-testid="history-{id}-empty">{empty}</p>
  {:else}
    <div class="chart">{@render chart()}</div>
    <details class="data">
      <summary>{tableSummary}</summary>
      <!-- svelte-ignore a11y_no_noninteractive_tabindex (keyboard access to a scrollable table) -->
      <div class="scroll" role="region" tabindex="0" aria-label={title}>
        {@render table()}
      </div>
    </details>
  {/if}
  <a class="btn secondary small csv" href={csvHref} download data-testid="csv-{id}">{csvLabel}</a>
</section>

<style>
  .chart {
    display: grid;
    gap: 8px;
    min-width: 0;
  }
  summary {
    cursor: pointer;
    min-height: 32px;
    display: flex;
    align-items: center;
    font-weight: 600;
    color: var(--primary-color);
  }
  .scroll {
    overflow-x: auto;
    margin-top: 8px;
  }
  .csv {
    justify-self: start;
  }
  .data :global(table) {
    border-collapse: collapse;
    width: 100%;
    min-width: 28rem;
    font-size: 0.9375rem;
    font-variant-numeric: tabular-nums;
  }
  .data :global(th),
  .data :global(td) {
    text-align: left;
    padding: 5px 10px 5px 0;
    border-top: 1px solid var(--divider-color);
  }
  .data :global(thead th) {
    border-top: 0;
  }
</style>
