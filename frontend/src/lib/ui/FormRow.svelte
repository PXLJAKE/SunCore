<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import type { Snippet } from 'svelte';

  interface Props {
    label: string;
    /** Help text under the control. */
    hint?: string;
    /** A validation message (already translated); marks the row invalid. */
    error?: string | null;
    /**
     * The control. It gets the id to put on itself (the label points to it) and the ids for
     * `aria-describedby` (hint and error), and whether it is invalid.
     */
    children: Snippet<[{ id: string; describedby: string | undefined; invalid: boolean }]>;
  }
  let { label, hint, error = null, children }: Props = $props();

  const uid = $props.id();
  const describedby = $derived(
    [hint ? `${uid}-hint` : '', error ? `${uid}-error` : ''].filter(Boolean).join(' ') || undefined,
  );
</script>

<div class="form-row" class:invalid={!!error}>
  <label class="label" for="{uid}-control">{label}</label>
  {@render children({ id: `${uid}-control`, describedby, invalid: !!error })}
  {#if hint}<p class="hint" id="{uid}-hint">{hint}</p>{/if}
  {#if error}<p class="error" id="{uid}-error">{error}</p>{/if}
</div>

<style>
  .form-row {
    display: grid;
    gap: var(--sc-space-1);
    min-width: 0;
  }
  .label {
    font-size: var(--sc-font-sm);
    font-weight: var(--sc-weight-strong);
  }
  .error {
    margin: 0;
    font-size: var(--sc-font-sm);
    color: var(--sc-error);
  }
  .invalid :global(.input) {
    border-color: var(--sc-error);
  }
</style>
