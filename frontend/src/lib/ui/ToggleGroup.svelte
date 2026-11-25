<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts" generics="V extends string">
  interface Option {
    value: V;
    /** Visible text; keep it short (one word). */
    label: string;
    /** Full accessible name when the visible label is a short form; must contain `label`. */
    name?: string;
  }
  interface Props {
    /** Accessible name of the group ("Mode of Pool pump"). */
    label: string;
    options: readonly Option[];
    /** The pressed option. */
    value: V;
    disabled?: boolean;
    /** Called for every press, also on the pressed option; the caller decides what happens. */
    onselect: (value: V) => void;
    /** Test id prefix of the buttons: `<prefix>-<value>`. */
    testid?: string;
  }
  let { label, options, value, disabled = false, onselect, testid }: Props = $props();
</script>

<!--
  Toggle buttons (aria-pressed), not a radio group: a mode change can open a confirmation or write
  to a device, so arrow keys must not change the selection by themselves. Every option is a tab stop.
-->
<div class="toggle-group" role="group" aria-label={label}>
  {#each options as opt (opt.value)}
    <button
      type="button"
      aria-pressed={opt.value === value}
      aria-label={opt.name}
      {disabled}
      data-testid={testid ? `${testid}-${opt.value}` : undefined}
      onclick={() => onselect(opt.value)}>{opt.label}</button
    >
  {/each}
</div>

<style>
  .toggle-group {
    display: flex;
    flex-wrap: wrap;
    gap: 2px;
    padding: 3px;
    border-radius: var(--sc-radius-md);
    background: var(--sc-surface-sunken);
    border: 1px solid var(--sc-outline);
    min-width: 0;
  }
  button {
    flex: 1 1 0;
    min-width: max-content;
    min-height: 38px;
    padding: 0 var(--sc-space-3);
    border: 0;
    border-radius: calc(var(--sc-radius-md) - 3px);
    background: transparent;
    color: var(--sc-text-muted);
    font: inherit;
    font-size: var(--sc-font-sm);
    font-weight: var(--sc-weight-strong);
    cursor: pointer;
    transition:
      background-color var(--sc-motion-fast) ease,
      color var(--sc-motion-fast) ease;
  }
  button:hover:not(:disabled) {
    color: var(--sc-text);
  }
  button[aria-pressed='true'] {
    background: var(--sc-surface);
    color: var(--sc-accent);
    box-shadow:
      0 0 0 1px var(--sc-outline),
      var(--sc-elev-1);
  }
  button:disabled {
    opacity: 0.55;
    cursor: not-allowed;
  }
</style>
