<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts" generics="V extends string">
  interface Option {
    value: V;
    label: string;
  }
  interface Props {
    /** Accessible name of the group. */
    label: string;
    options: readonly Option[];
    value: V;
    onchange?: (value: V) => void;
    testid?: string;
  }
  let { label, options, value = $bindable(), onchange, testid }: Props = $props();

  let buttons: HTMLButtonElement[] = $state([]);

  function pick(v: V): void {
    if (v === value) return;
    value = v;
    onchange?.(v);
  }

  // A radio group: one tab stop, arrow keys move and select (WAI-ARIA radio group pattern).
  function onkeydown(e: KeyboardEvent, index: number): void {
    const step =
      e.key === 'ArrowRight' || e.key === 'ArrowDown'
        ? 1
        : e.key === 'ArrowLeft' || e.key === 'ArrowUp'
          ? -1
          : 0;
    const next =
      step !== 0
        ? (index + step + options.length) % options.length
        : e.key === 'Home'
          ? 0
          : e.key === 'End'
            ? options.length - 1
            : -1;
    if (next < 0) return;
    e.preventDefault();
    const opt = options[next];
    if (!opt) return;
    pick(opt.value);
    buttons[next]?.focus();
  }
</script>

<div class="segmented" role="radiogroup" aria-label={label} data-testid={testid}>
  {#each options as opt, i (opt.value)}
    <button
      bind:this={buttons[i]}
      type="button"
      role="radio"
      aria-checked={opt.value === value}
      tabindex={opt.value === value ? 0 : -1}
      onclick={() => pick(opt.value)}
      onkeydown={(e) => onkeydown(e, i)}>{opt.label}</button
    >
  {/each}
</div>

<style>
  .segmented {
    display: inline-flex;
    flex-wrap: wrap;
    gap: 2px;
    padding: 3px;
    border-radius: var(--sc-radius-pill);
    background: var(--sc-surface-sunken);
    border: 1px solid var(--sc-outline);
  }
  button {
    min-height: 38px;
    padding: 0 var(--sc-space-4);
    border: 0;
    border-radius: var(--sc-radius-pill);
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
  button:hover {
    color: var(--sc-text);
  }
  button[aria-checked='true'] {
    background: var(--sc-surface);
    color: var(--sc-text);
    box-shadow: var(--sc-elev-1);
    outline: 1px solid var(--sc-outline);
  }
</style>
