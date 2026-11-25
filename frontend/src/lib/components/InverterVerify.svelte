<!-- SPDX-License-Identifier: Apache-2.0 -->
<!--
  The inverter limit verification (plan phase 7 WP8, PR 7-J): a card with the adapter's state
  and a dialog that walks through the test. The panel never chooses a limit: the server proposes
  it, the user confirms the one write, everything else (release, read-back, aborts) is the
  server's and the safety gate's. Closing the dialog while the test runs aborts it (release).
-->
<script lang="ts">
  import { onDestroy, onMount } from 'svelte';
  import { verificationApi } from '../api/client';
  import { describeError } from '../api/errors';
  import type { Reason, VerificationView } from '../api/types';
  import { t, tDynamic, tPercent, tPower, tTime } from '../i18n/index.svelte';
  import ReasonList from './ReasonList.svelte';

  const POLL_MS = 2000;
  const RUNNING = new Set(['checking', 'ready', 'writing', 'observing', 'releasing']);

  const uid = $props.id();
  let view = $state<VerificationView | null>(null);
  let open = $state(false);
  let busy = $state(false);
  let refused = $state<Reason[]>([]);
  let error = $state<string | null>(null);
  let dialog: HTMLDialogElement | undefined = $state();
  let timer: ReturnType<typeof setInterval> | undefined;

  const running = $derived(view !== null && RUNNING.has(view.phase));
  const phase = $derived(view?.phase ?? 'idle');

  async function load(): Promise<void> {
    try {
      view = await verificationApi.get();
      error = null;
    } catch (e) {
      error = describeError(e).key;
    }
  }

  function poll(on: boolean): void {
    if (timer !== undefined) clearInterval(timer);
    timer = on ? setInterval(() => void load(), POLL_MS) : undefined;
  }

  async function command(fn: typeof verificationApi.start): Promise<void> {
    busy = true;
    error = null;
    try {
      const answer = await fn();
      if (answer.ok) {
        view = answer.view;
        refused = [];
      } else {
        refused = answer.reasons;
      }
    } catch (e) {
      error = describeError(e).key;
    } finally {
      busy = false;
    }
  }

  function show(): void {
    refused = [];
    open = true;
    poll(true);
    void load();
  }

  /** Closing (button, Escape) while the test runs gives the user's limit back at once. */
  async function close(): Promise<void> {
    if (running) await command(verificationApi.abort);
    open = false;
    poll(false);
  }

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

  onMount(() => void load());
  onDestroy(() => poll(false));

  function limitText(v: VerificationView): string {
    const limit = v.test_limit;
    if (!limit) return '';
    if (limit.max_ac_pct !== null && limit.max_ac_pct !== undefined) {
      return t('verify.limit_pct', {
        pct: tPercent(limit.max_ac_pct),
        power: tPower(v.test_limit_w ?? 0),
      });
    }
    return tPower(limit.max_export_w ?? 0);
  }

  function baselineText(v: VerificationView): string {
    const b = v.baseline;
    if (!b) return '–';
    if (b.max_ac_pct !== null && b.max_ac_pct !== undefined) return tPercent(b.max_ac_pct);
    return tPower(b.max_export_w ?? 0);
  }
</script>

<section class="card verify" aria-labelledby="{uid}-title" data-testid="inverter-limit">
  <h3 id="{uid}-title">{t('inverter.title')}</h3>
  <p class="hint">{t('inverter.hint')}</p>
  {#if view === null}
    <p class="hint">{error ? tDynamic(error, undefined, 'api.error.unknown') : t('app.loading')}</p>
  {:else if !view.adapter}
    <p class="notice info" data-testid="inverter-why">{t('verify.no_adapter_hint')}</p>
  {:else}
    <p data-testid="inverter-verified" class:ok={view.verified}>
      {view.verified ? t('verify.status_verified') : t('verify.status_unverified')}
    </p>
    {#if view.last}
      <p class="hint" data-testid="inverter-last">
        {view.last.outcome === 'passed'
          ? t('verify.last_passed', { at: tTime(new Date(view.last.at)) })
          : t('verify.last_failed', {
              at: tTime(new Date(view.last.at)),
              reason: tDynamic(view.last.reason ?? 'verify.aborted', undefined),
            })}
      </p>
    {/if}
  {/if}
  <button
    type="button"
    class="btn secondary"
    data-testid="inverter-verify-open"
    disabled={view === null || !view.adapter}
    onclick={show}>{t('inverter.verify')}</button
  >
</section>

<dialog
  bind:this={dialog}
  aria-labelledby="{uid}-dlg-title"
  aria-describedby="{uid}-dlg-body"
  data-testid="verify-dialog"
  oncancel={(e) => {
    e.preventDefault(); // Escape: the same as the close button (aborts a running test)
    void close();
  }}
>
  <h2 id="{uid}-dlg-title">{t('verify.title')}</h2>
  <div id="{uid}-dlg-body" class="body" aria-live="polite" data-testid="verify-phase-{phase}">
    {#if phase === 'idle'}
      <p>{t('verify.intro')}</p>
      <ul class="facts">
        <li>{t('verify.intro_assisted')}</li>
        <li>{t('verify.intro_import')}</li>
        <li>{t('verify.intro_abort')}</li>
      </ul>
    {:else if phase === 'checking' && view}
      <p>{t('verify.checking', { done: Math.round(view.stable_s), of: view.stable_needed_s })}</p>
      <progress max={view.stable_needed_s} value={view.stable_s}></progress>
    {:else if phase === 'ready' && view}
      <p data-testid="verify-proposal">
        {t('verify.ready', { limit: limitText(view), baseline: baselineText(view) })}
      </p>
      <p class="hint">
        {t('verify.ready_hint', {
          until: view.confirm_by ? tTime(new Date(view.confirm_by)) : '–',
        })}
      </p>
    {:else if (phase === 'writing' || phase === 'observing') && view}
      <p>{t('verify.observing', { limit: limitText(view) })}</p>
    {:else if phase === 'releasing'}
      <p>{t('verify.releasing')}</p>
    {:else if phase === 'passed' && view}
      <p class="notice ok" data-testid="verify-result">
        {view.semantics === 'ac_limit' ? t('verify.passed_ac') : t('verify.passed_export')}
      </p>
    {:else if phase === 'failed'}
      <p class="notice warn" data-testid="verify-result">{t('verify.failed')}</p>
    {:else if phase === 'aborted'}
      <p class="notice info" data-testid="verify-result">{t('verify.aborted_text')}</p>
    {/if}

    {#if view && running && view.export_w !== null && view.export_w !== undefined}
      <p class="hint" data-testid="verify-live">
        {t('verify.live', {
          export: tPower(view.export_w),
          house: tPower(view.house_w ?? 0),
        })}
      </p>
    {/if}
    {#if view && view.reasons.length > 0 && phase !== 'idle'}
      <ReasonList reasons={view.reasons} />
    {/if}
    {#if refused.length > 0}
      <div class="notice warn" role="alert" data-testid="verify-refused">
        <p>{t('verify.refused')}</p>
        <ReasonList reasons={refused} />
      </div>
    {/if}
    {#if error}
      <p class="notice error" role="alert">{tDynamic(error, undefined, 'api.error.unknown')}</p>
    {/if}
  </div>
  <div class="actions">
    <!-- svelte-ignore a11y_autofocus -->
    <button
      type="button"
      class="btn secondary"
      autofocus
      data-testid="verify-close"
      disabled={busy && !running}
      onclick={() => void close()}>{running ? t('verify.cancel') : t('action.close')}</button
    >
    {#if !running}
      <button
        type="button"
        class="btn"
        data-testid="verify-start"
        disabled={busy}
        onclick={() => void command(verificationApi.start)}
        >{phase === 'idle' ? t('verify.start') : t('verify.again')}</button
      >
    {:else if phase === 'ready'}
      <button
        type="button"
        class="btn"
        data-testid="verify-confirm"
        disabled={busy}
        onclick={() => void command(verificationApi.confirm)}>{t('verify.confirm')}</button
      >
    {/if}
  </div>
</dialog>

<style>
  .verify {
    display: grid;
    gap: var(--sc-space-2);
  }
  .verify h3 {
    margin: 0;
    font-size: 1rem;
  }
  .verify p {
    margin: 0;
  }
  .ok {
    color: var(--sc-ok);
  }
  dialog {
    max-width: min(34rem, calc(100vw - 32px));
    padding: var(--sc-space-5);
    border: 1px solid var(--sc-outline);
    border-radius: var(--sc-radius-lg);
    background: var(--sc-surface-raised);
    color: var(--sc-text);
    box-shadow: var(--sc-elev-2);
  }
  dialog::backdrop {
    background: var(--sc-scrim);
  }
  h2 {
    font-size: 1.125rem;
    margin-bottom: 8px;
  }
  .body {
    display: grid;
    gap: 8px;
  }
  .body p {
    margin: 0;
  }
  .facts {
    margin: 0;
    padding-left: 20px;
  }
  progress {
    width: 100%;
  }
  .actions {
    display: flex;
    flex-wrap: wrap;
    justify-content: flex-end;
    gap: 8px;
    margin-top: 16px;
  }
</style>
