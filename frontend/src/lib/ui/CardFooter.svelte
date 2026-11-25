<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts" module>
  import type { Snippet } from 'svelte';

  export interface FooterItem {
    /** Stable key of the item inside the card. */
    id: string;
    /** Text of the toggle ("Why?", "Take over manually"). */
    label: string;
    /** Test id of the panel. */
    testid?: string;
    panel: Snippet;
  }
</script>

<script lang="ts">
  import Icon from './Icon.svelte';

  interface Props {
    items: readonly FooterItem[];
  }
  let { items }: Props = $props();

  const uid = $props.id();
  let open = $state<Record<string, boolean>>({});
</script>

<!--
  The quiet end of a card: secondary actions as disclosure buttons in one row, their panels below
  the row at full width. Panels stay in the DOM (hidden), so their content is always reachable by
  the toggle and searchable in tests.
-->
<div class="card-footer">
  <div class="toggles">
    {#each items as item (item.id)}
      <button
        type="button"
        class="toggle"
        aria-expanded={open[item.id] ?? false}
        aria-controls="{uid}-{item.id}"
        onclick={() => (open[item.id] = !open[item.id])}
      >
        {item.label}
        <span class="chev" class:up={open[item.id]}><Icon name="chevron" size={16} /></span>
      </button>
    {/each}
  </div>
  {#each items as item (item.id)}
    <div class="panel" id="{uid}-{item.id}" hidden={!open[item.id]} data-testid={item.testid}>
      {@render item.panel()}
    </div>
  {/each}
</div>

<style>
  /* Lives in the footer of a Card, which draws the rule above it. */
  .card-footer {
    flex: 1 1 100%;
    display: grid;
    gap: var(--sc-space-2);
    min-width: 0;
    margin-top: calc(-1 * var(--sc-space-2));
  }
  .toggles {
    display: flex;
    flex-wrap: wrap;
    gap: 0 var(--sc-space-5);
  }
  .toggle {
    display: inline-flex;
    align-items: center;
    gap: var(--sc-space-1);
    min-height: var(--sc-target);
    padding: 0;
    border: 0;
    background: none;
    color: var(--sc-accent);
    font: inherit;
    font-size: var(--sc-font-sm);
    font-weight: var(--sc-weight-strong);
    cursor: pointer;
  }
  .chev {
    display: inline-flex;
    transition: transform var(--sc-motion-fast) ease;
  }
  .chev.up {
    transform: rotate(180deg);
  }
  .panel {
    display: grid;
    gap: var(--sc-space-2);
    min-width: 0;
    padding: var(--sc-space-3);
    border-radius: var(--sc-radius-md);
    background: var(--sc-surface-sunken);
    font-size: var(--sc-font-sm);
  }
  .panel[hidden] {
    display: none;
  }
</style>
