<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import type { Snippet } from 'svelte';
  import Icon from './Icon.svelte';
  import type { IconName } from './icons';

  interface Props {
    title: string;
    text?: string;
    icon?: IconName;
    testid?: string;
    /** The way out: a button or link. */
    actions?: Snippet;
  }
  let { title, text, icon = 'empty', testid, actions }: Props = $props();
</script>

<div class="empty-state" data-testid={testid}>
  <span class="art"><Icon name={icon} size={28} /></span>
  <p class="title">{title}</p>
  {#if text}<p class="hint">{text}</p>{/if}
  {#if actions}<div class="actions">{@render actions()}</div>{/if}
</div>

<style>
  .empty-state {
    display: grid;
    justify-items: center;
    gap: var(--sc-space-2);
    padding: var(--sc-space-6) var(--sc-space-4);
    text-align: center;
  }
  .art {
    display: inline-grid;
    place-items: center;
    width: 56px;
    height: 56px;
    margin-bottom: var(--sc-space-2);
    border-radius: 50%;
    background: var(--sc-accent-bg);
    color: var(--sc-accent);
  }
  .title {
    margin: 0;
    font-weight: var(--sc-weight-strong);
    font-size: var(--sc-font-lg);
  }
  .hint {
    max-width: 36rem;
  }
  .actions {
    display: flex;
    flex-wrap: wrap;
    justify-content: center;
    gap: var(--sc-space-2);
    margin-top: var(--sc-space-2);
  }
</style>
