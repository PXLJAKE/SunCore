<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import type { Snippet } from 'svelte';
  import Icon from './Icon.svelte';
  import type { IconName } from './icons';

  export type ChipTone = 'neutral' | 'accent' | 'ok' | 'warn' | 'error' | 'info';

  interface Props {
    tone?: ChipTone;
    icon?: IconName;
    testid?: string;
    children: Snippet;
  }
  let { tone = 'neutral', icon, testid, children }: Props = $props();
</script>

<span class="chip {tone}" data-testid={testid}
  >{#if icon}<Icon name={icon} size={14} />{/if}{@render children()}</span
>

<style>
  .chip {
    display: inline-flex;
    align-items: center;
    gap: var(--sc-space-1);
    min-height: 24px;
    padding: 0 var(--sc-space-2);
    border-radius: var(--sc-radius-pill);
    font-size: var(--sc-font-xs);
    font-weight: var(--sc-weight-strong);
    line-height: 1;
    white-space: nowrap;
    background: var(--sc-surface-sunken);
    color: var(--sc-text-muted);
  }
  .accent {
    background: var(--sc-accent-bg);
    color: var(--sc-accent);
  }
  .ok {
    background: var(--sc-ok-bg);
    color: var(--sc-ok);
  }
  .warn {
    background: var(--sc-warn-bg);
    color: var(--sc-warn);
  }
  .error {
    background: var(--sc-error-bg);
    color: var(--sc-error);
  }
  .info {
    background: var(--sc-info-bg);
    color: var(--sc-info);
  }
</style>
