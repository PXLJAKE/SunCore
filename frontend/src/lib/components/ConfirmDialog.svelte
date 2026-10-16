<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import type { Snippet } from 'svelte';
  import { t } from '../i18n/index.svelte';

  interface Props {
    open: boolean;
    title: string;
    confirmLabel: string;
    /** The confirm button is the dangerous one (red); cancel keeps the initial focus. */
    danger?: boolean;
    busy?: boolean;
    onconfirm: () => void;
    oncancel: () => void;
    children: Snippet;
    testid?: string;
  }
  let {
    open,
    title,
    confirmLabel,
    danger = false,
    busy = false,
    onconfirm,
    oncancel,
    children,
    testid = 'confirm-dialog',
  }: Props = $props();

  const uid = $props.id();
  let dialog: HTMLDialogElement | undefined = $state();

  // The native modal dialog traps focus, closes on Escape and restores the focus afterwards.
  $effect(() => {
    if (!dialog) return;
    if (open && !dialog.open) {
      if (typeof dialog.showModal === 'function') dialog.showModal();
      else dialog.setAttribute('open', '');
    } else if (!open && dialog.open) {
      if (typeof dialog.close === 'function') dialog.close();
      else dialog.removeAttribute('open');
    }
  });
</script>

<dialog
  bind:this={dialog}
  aria-labelledby="{uid}-title"
  aria-describedby="{uid}-body"
  data-testid={testid}
  oncancel={(e) => {
    e.preventDefault(); // the parent owns `open`
    oncancel();
  }}
>
  <h2 id="{uid}-title">{title}</h2>
  <div id="{uid}-body" class="body">{@render children()}</div>
  <div class="actions">
    <!-- svelte-ignore a11y_autofocus -->
    <button type="button" class="btn secondary" autofocus onclick={oncancel}
      >{t('action.cancel')}</button
    >
    <button
      type="button"
      class="btn"
      class:danger-fill={danger}
      disabled={busy}
      data-testid="{testid}-confirm"
      onclick={onconfirm}>{confirmLabel}</button
    >
  </div>
</dialog>

<style>
  dialog {
    max-width: min(32rem, calc(100vw - 32px));
    padding: var(--sc-gap);
    border: 1px solid var(--divider-color);
    border-radius: var(--sc-radius);
    background: var(--card-background-color);
    color: var(--primary-text-color);
  }
  dialog::backdrop {
    background: rgb(0 0 0 / 0.55);
  }
  h2 {
    font-size: 1.125rem;
    margin-bottom: 8px;
  }
  .body {
    display: grid;
    gap: 8px;
  }
  .body :global(p) {
    margin: 0;
  }
  .actions {
    display: flex;
    flex-wrap: wrap;
    justify-content: flex-end;
    gap: 8px;
    margin-top: 16px;
  }
  .danger-fill {
    background: var(--error-color);
    border-color: var(--error-color);
    color: var(--card-background-color);
  }
</style>
