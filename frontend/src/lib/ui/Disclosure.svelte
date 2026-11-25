<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import type { Snippet } from 'svelte';
  import Icon from './Icon.svelte';

  interface Props {
    /** Text of the toggle ("Why?", "Advanced settings"). */
    summary: string;
    open?: boolean;
    testid?: string;
    children: Snippet;
  }
  let { summary, open = $bindable(false), testid, children }: Props = $props();
</script>

<!-- Native details/summary: keyboard, state and announcement come from the browser. -->
<details class="disclosure" bind:open data-testid={testid}>
  <summary>
    <span class="chev"><Icon name="chevron" size={18} /></span>
    {summary}
  </summary>
  <div class="content">{@render children()}</div>
</details>

<style>
  .disclosure {
    min-width: 0;
  }
  summary {
    display: inline-flex;
    align-items: center;
    gap: var(--sc-space-1);
    min-height: var(--sc-target);
    color: var(--sc-accent);
    font-weight: var(--sc-weight-strong);
    cursor: pointer;
    list-style: none;
  }
  summary::-webkit-details-marker {
    display: none;
  }
  .chev {
    display: inline-flex;
    transform: rotate(-90deg);
    transition: transform var(--sc-motion-fast) ease;
  }
  details[open] .chev {
    transform: none;
  }
  .content {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    gap: var(--sc-space-2);
    padding: var(--sc-space-1) 0 var(--sc-space-2) var(--sc-space-5);
  }
</style>
