<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import type { Snippet } from 'svelte';
  import Icon from './Icon.svelte';
  import type { IconName } from './icons';

  /** Severity, also the stacking order in the shell (error first). */
  export type BannerTone = 'error' | 'warn' | 'info' | 'ok';

  interface Props {
    tone?: BannerTone;
    /** A short bold lead-in. */
    title?: string;
    /** `status` for a lasting state; `alert` only for something that just went wrong. */
    role?: 'status' | 'alert' | 'note';
    testid?: string;
    actions?: Snippet;
    children?: Snippet;
  }
  let { tone = 'info', title, role = 'status', testid, actions, children }: Props = $props();

  const ICON: Record<BannerTone, IconName> = {
    error: 'error',
    warn: 'warn',
    info: 'info',
    ok: 'ok',
  };
</script>

<div class="banner {tone}" {role} data-testid={testid}>
  <span class="icon-wrap"><Icon name={ICON[tone]} /></span>
  <div class="text">
    {#if title}<strong>{title}</strong>{/if}
    {#if children}<span>{@render children()}</span>{/if}
  </div>
  {#if actions}<div class="actions">{@render actions()}</div>{/if}
</div>

<style>
  .banner {
    display: flex;
    align-items: flex-start;
    gap: var(--sc-space-3);
    padding: var(--sc-space-3) var(--sc-space-4);
    border-radius: var(--sc-radius-md);
    border: 1px solid var(--sc-outline);
    font-size: var(--sc-font-sm);
    color: var(--sc-text);
  }
  .icon-wrap {
    display: inline-flex;
    padding-top: 1px;
  }
  .text {
    display: flex;
    flex-wrap: wrap;
    gap: 2px var(--sc-space-2);
    flex: 1;
    min-width: 0;
  }
  .actions {
    display: flex;
    gap: var(--sc-space-2);
  }
  .error {
    background: var(--sc-error-bg);
    border-color: var(--sc-error);
  }
  .error .icon-wrap {
    color: var(--sc-error);
  }
  .warn {
    background: var(--sc-warn-bg);
  }
  .warn .icon-wrap {
    color: var(--sc-warn);
  }
  .info {
    background: var(--sc-info-bg);
  }
  .info .icon-wrap {
    color: var(--sc-info);
  }
  .ok {
    background: var(--sc-ok-bg);
  }
  .ok .icon-wrap {
    color: var(--sc-ok);
  }
</style>
