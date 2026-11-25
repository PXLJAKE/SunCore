<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  // One chart card of the history view: title, hint, the chart, the CSV link and the data as a
  // table (the text alternative). The section decides nothing about the data.
  import type { Snippet } from 'svelte';
  import { tDynamic } from '../i18n/index.svelte';
  import Card from '../ui/Card.svelte';
  import Disclosure from '../ui/Disclosure.svelte';
  import Icon from '../ui/Icon.svelte';

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
</script>

<Card {title} testid="history-{id}">
  <div class="layout">
    <div class="text">
      <p class="hint">{hint}</p>
      {#if caption}<p class="hint caption" data-testid="history-{id}-caption">{caption}</p>{/if}
    </div>
    {#if error}
      <p class="notice error state" role="alert">
        {tDynamic(error, undefined, 'api.error.unknown')}
      </p>
    {:else if empty}
      <p class="hint state" data-testid="history-{id}-empty">{empty}</p>
    {:else}
      <!-- The chart's own PNG button joins the CSV link in the last row (see the grid below). -->
      <div class="plot">{@render chart()}</div>
      <div class="data">
        <Disclosure summary={tableSummary}>
          <!-- svelte-ignore a11y_no_noninteractive_tabindex (keyboard access to a scrollable table) -->
          <div class="table-scroll" role="region" tabindex="0" aria-label={title}>
            {@render table()}
          </div>
        </Disclosure>
      </div>
    {/if}
    <a class="btn ghost small csv" href={csvHref} download data-testid="csv-{id}"
      ><Icon name="download" size={16} />{csvLabel}</a
    >
  </div>
</Card>

<style>
  /*
   * Text, chart, then one quiet row: the table disclosure on the left, PNG and CSV on the right.
   * The chart wrapper is transparent to the grid, so the chart's PNG button can join that row.
   */
  .layout {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto auto;
    grid-template-areas:
      'text text text'
      'chart chart chart'
      'data png csv';
    gap: var(--sc-space-2) var(--sc-space-2);
    align-items: start;
    min-width: 0;
  }
  .text {
    grid-area: text;
    display: grid;
    gap: var(--sc-space-1);
  }
  .caption {
    font-variant-numeric: tabular-nums;
  }
  .state {
    grid-area: chart;
  }
  .plot {
    display: contents;
  }
  .plot > :global(*) {
    grid-area: chart;
    min-width: 0;
  }
  .plot > :global(.png) {
    grid-area: png;
  }
  .csv {
    grid-area: csv;
  }
  .data {
    grid-area: data;
    min-width: 0;
  }
  .data :global(.data-table) {
    min-width: 28rem;
  }
  /* Phone: the disclosure gets the full width, the two downloads go below it. */
  @media (max-width: 599px) {
    .layout {
      grid-template-columns: auto auto minmax(0, 1fr);
      grid-template-areas:
        'text text text'
        'chart chart chart'
        'data data data'
        'png csv .';
    }
  }
</style>
