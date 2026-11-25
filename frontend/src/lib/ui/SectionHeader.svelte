<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import type { Snippet } from 'svelte';

  interface Props {
    title: string;
    level?: 2 | 3;
    /** One sentence under the title. */
    description?: string;
    /** Element id of the heading, for aria-labelledby of the section it heads. */
    id?: string;
    actions?: Snippet;
  }
  let { title, level = 3, description, id, actions }: Props = $props();
</script>

<div class="section-header" class:page={level === 2}>
  <div class="text">
    <svelte:element this={`h${level}`} {id}>{title}</svelte:element>
    {#if description}<p class="hint">{description}</p>{/if}
  </div>
  {#if actions}<div class="actions">{@render actions()}</div>{/if}
</div>

<style>
  .section-header {
    display: flex;
    flex-wrap: wrap;
    align-items: flex-end;
    justify-content: space-between;
    gap: var(--sc-space-2) var(--sc-space-4);
    min-width: 0;
  }
  .text {
    display: grid;
    gap: var(--sc-space-1);
    min-width: 0;
  }
  h3 {
    font-size: var(--sc-font-sm);
    font-weight: var(--sc-weight-strong);
    letter-spacing: 0.04em;
    text-transform: uppercase;
    color: var(--sc-text-muted);
  }
  .page h2 {
    font-size: var(--sc-font-xl);
  }
  @media (min-width: 600px) {
    .page h2 {
      font-size: var(--sc-font-2xl);
    }
  }
  .actions {
    display: flex;
    flex-wrap: wrap;
    gap: var(--sc-space-2);
  }
</style>
