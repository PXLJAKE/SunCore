<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import type { Snippet } from 'svelte';
  import type { HTMLButtonAttributes } from 'svelte/elements';
  import Icon from './Icon.svelte';
  import type { IconName } from './icons';

  type Variant = 'primary' | 'secondary' | 'ghost' | 'danger' | 'danger-fill';

  interface Props extends Omit<HTMLButtonAttributes, 'class'> {
    variant?: Variant;
    size?: 'regular' | 'small';
    icon?: IconName;
    /** While true the button is disabled and announces itself as busy. */
    busy?: boolean;
    /** Renders a link that looks like a button. */
    href?: string;
    testid?: string;
    children: Snippet;
  }
  let {
    variant = 'primary',
    size = 'regular',
    icon,
    busy = false,
    href,
    testid,
    disabled = false,
    type = 'button',
    children,
    ...rest
  }: Props = $props();

  const cls = $derived(
    [
      'btn',
      'sc-button',
      variant === 'primary' ? '' : variant,
      size === 'small' ? 'small' : '',
      busy ? 'busy' : '',
    ]
      .filter(Boolean)
      .join(' '),
  );
</script>

{#if href}
  <a class={cls} {href} data-testid={testid}>
    {#if icon}<Icon name={icon} size={18} />{/if}
    {@render children()}
  </a>
{:else}
  <button
    {...rest}
    {type}
    class={cls}
    disabled={disabled || busy}
    aria-busy={busy || undefined}
    data-testid={testid}
  >
    {#if icon}<Icon name={icon} size={18} />{/if}
    {@render children()}
  </button>
{/if}

<style>
  .danger-fill {
    background: var(--sc-error);
    border-color: var(--sc-error);
    color: var(--sc-error-fg);
  }
  .danger-fill:hover:not(:disabled) {
    background: var(--sc-error);
  }
  .busy {
    cursor: progress;
  }
</style>
