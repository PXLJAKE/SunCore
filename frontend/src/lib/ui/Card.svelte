<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import type { Snippet } from 'svelte';
  import Icon from './Icon.svelte';
  import type { IconName } from './icons';

  interface Props {
    /** Heading of the card; without it the card has no header. */
    title?: string;
    /** Heading level inside the view (the view title is level 2). */
    level?: 2 | 3 | 4;
    icon?: IconName;
    variant?: 'default' | 'attention' | 'quiet';
    density?: 'regular' | 'compact';
    /** The header becomes a disclosure button that shows and hides the body. */
    collapsible?: boolean;
    open?: boolean;
    testid?: string;
    /** A status chip next to the title. */
    status?: Snippet;
    /** Buttons or links at the end of the header. */
    actions?: Snippet;
    footer?: Snippet;
    /** An `article` (a self-contained item in a grid, e.g. a device) instead of a `section`. */
    article?: boolean;
    /** Element id, e.g. a scroll and focus target (`#/?device=`); the card gets tabindex -1. */
    id?: string;
    /** Further attributes of the root element (`data-*`). */
    [attr: `data-${string}`]: string | undefined;
    children: Snippet;
  }
  let {
    title,
    level = 3,
    icon,
    variant = 'default',
    density = 'regular',
    collapsible = false,
    open = $bindable(true),
    testid,
    status,
    actions,
    footer,
    article = false,
    id,
    children,
    ...data
  }: Props = $props();

  const uid = $props.id();
  const expanded = $derived(!collapsible || open);
</script>

<svelte:element
  this={article ? 'article' : 'section'}
  {...data}
  {id}
  tabindex={id ? -1 : undefined}
  class="card sc-card {variant} {density}"
  aria-labelledby={title ? `${uid}-title` : undefined}
  data-testid={testid}
>
  {#if title}
    <header class="head">
      <svelte:element this={`h${level}`} class="title" id="{uid}-title">
        {#if collapsible}
          <button
            type="button"
            class="toggle"
            aria-expanded={open}
            aria-controls="{uid}-body"
            onclick={() => (open = !open)}
          >
            {#if icon}<Icon name={icon} />{/if}
            <span>{title}</span>
            <span class="chev" class:up={open}><Icon name="chevron" size={18} /></span>
          </button>
        {:else}
          {#if icon}<Icon name={icon} />{/if}
          <span>{title}</span>
        {/if}
      </svelte:element>
      {#if status}<div class="status">{@render status()}</div>{/if}
      {#if actions}<div class="actions">{@render actions()}</div>{/if}
    </header>
  {/if}
  <div class="body" id="{uid}-body" hidden={!expanded}>
    {@render children()}
  </div>
  {#if footer && expanded}
    <footer class="foot">{@render footer()}</footer>
  {/if}
</svelte:element>

<style>
  /* A column: in a grid of cards each card stretches to the row and the footer sits at the end. */
  .sc-card {
    display: flex;
    flex-direction: column;
    gap: var(--sc-space-4);
    min-width: 0;
  }
  .compact {
    gap: var(--sc-space-3);
    padding: var(--sc-space-3) var(--sc-space-4);
  }
  .attention {
    border-color: var(--sc-warn);
    box-shadow:
      inset 4px 0 0 var(--sc-warn),
      var(--sc-elev-1);
  }
  .quiet {
    background: transparent;
    box-shadow: none;
  }
  .head {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--sc-space-2) var(--sc-space-3);
    min-width: 0;
  }
  .title {
    display: flex;
    align-items: center;
    gap: var(--sc-space-2);
    flex: 1 1 12rem;
    min-width: 0;
    font-size: var(--sc-font-lg);
  }
  .title :global(.icon) {
    color: var(--sc-text-muted);
  }
  .toggle {
    display: flex;
    align-items: center;
    gap: var(--sc-space-2);
    width: 100%;
    min-height: var(--sc-target);
    margin: calc(-1 * var(--sc-space-2)) 0;
    padding: 0;
    border: 0;
    background: none;
    color: inherit;
    font: inherit;
    text-align: start;
    cursor: pointer;
  }
  .chev {
    display: inline-flex;
    margin-inline-start: auto;
    transition: transform var(--sc-motion-normal) ease;
  }
  .chev.up {
    transform: rotate(180deg);
  }
  .status,
  .actions {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--sc-space-2);
  }
  .body {
    display: grid;
    gap: var(--sc-space-3);
    min-width: 0;
  }
  .body[hidden] {
    display: none;
  }
  .foot {
    display: flex;
    flex-wrap: wrap;
    gap: var(--sc-space-2);
    margin-top: auto;
    padding-top: var(--sc-space-3);
    border-top: 1px solid var(--sc-outline);
  }
</style>
