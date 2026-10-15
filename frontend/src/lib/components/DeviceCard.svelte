<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import type { DeviceControlState, OperatingMode } from '../api/types';
  import { holdDay, MODE_ORDER, stateView } from '../control/present';
  import {
    t,
    tDateTime,
    tDynamic,
    tNumber,
    tPower,
    tTime,
    type MessageKey,
  } from '../i18n/index.svelte';
  import { control } from '../stores/control.svelte';
  import ConfirmDialog from './ConfirmDialog.svelte';
  import OverrideForm from './OverrideForm.svelte';
  import ReasonList from './ReasonList.svelte';

  interface Props {
    device: DeviceControlState;
    /** Reference time for "held until tomorrow" (injected for tests). */
    now?: Date;
  }
  let { device, now }: Props = $props();

  const uid = $props.id();
  const MODE_LABEL: Record<OperatingMode, MessageKey> = {
    shadow: 'mode.shadow',
    assisted: 'mode.assisted',
    automatic: 'mode.automatic',
  };
  const KIND_LABEL: Record<string, MessageKey> = {
    accepted: 'decision.kind.accepted',
    rejected: 'decision.kind.rejected',
    shadow: 'decision.kind.shadow',
    proposed: 'decision.kind.proposed',
    held: 'decision.kind.held',
    failed: 'decision.kind.failed',
  };

  const view = $derived(stateView(device));
  const estimate = $derived(device.estimate);
  const busy = $derived(control.busy[device.device_id] ?? false);
  const error = $derived(control.errors[device.device_id] ?? null);
  const limitedByGlobal = $derived(device.effective_mode !== device.device_mode);
  const num = (v: number): string => tNumber(v, { maximumFractionDigits: 1 });

  function stateText(): string {
    switch (view.kind) {
      case 'on':
        return t('card.state.on');
      case 'off':
        return t('card.state.off');
      case 'step':
        return t('card.state.step', { step: view.step });
      case 'level':
        return t('card.state.level', { value: num(view.value) });
      case 'setpoint':
        return view.base == null
          ? t('card.state.setpoint', { value: num(view.setpoint) })
          : t('card.state.setpoint_base', { value: num(view.setpoint), base: num(view.base) });
      default:
        return t('card.state.unknown');
    }
  }

  function heldText(until: string | null): string {
    if (!until) return t('card.held_indefinitely');
    const at = new Date(until);
    const day = holdDay(at, now ?? new Date());
    if (day === 'today') return t('card.held_today', { time: tTime(at) });
    if (day === 'tomorrow') return t('card.held_tomorrow', { time: tTime(at) });
    return t('card.held_later', { when: tDateTime(at) });
  }

  // --- mode switch -----------------------------------------------------------------------
  /** The device and token the user was shown when the dialog opened (sent as is, see 409). */
  let pending = $state<{ identity: string } | null>(null);

  function choose(mode: OperatingMode): void {
    if (mode === device.device_mode || busy) return;
    control.clearError(device.device_id);
    if (mode === 'automatic') {
      pending = { identity: device.identity };
      return;
    }
    void control.setDeviceMode({ deviceId: device.device_id, mode, identity: device.identity });
  }

  async function confirmAutomatic(): Promise<void> {
    if (!pending) return;
    const identity = pending.identity;
    pending = null;
    await control.setDeviceMode({ deviceId: device.device_id, mode: 'automatic', identity });
  }
</script>

<article
  class="card dev"
  class:held={device.held}
  aria-labelledby="{uid}-name"
  data-testid="device-{device.device_id}"
  data-mode={device.effective_mode}
>
  <header>
    <h3 id="{uid}-name">{device.name}</h3>
    <span
      class="chip-tag"
      class:warn={device.effective_mode === 'automatic'}
      data-testid="effective-mode">{t(MODE_LABEL[device.effective_mode])}</span
    >
  </header>

  <dl class="facts">
    <div>
      <dt>{t('card.state')}</dt>
      <dd data-testid="state">{stateText()}</dd>
    </div>
    <div>
      <dt>{t('card.power')}</dt>
      <dd data-testid="power">
        {#if estimate}
          {t('card.power_estimate', {
            value: tPower(estimate.value_w),
            uncertainty: tPower(estimate.uncertainty_w),
          })}
        {:else}
          {t('card.power_unknown')}
        {/if}
      </dd>
    </div>
  </dl>

  {#if device.held || device.release_pending || device.paused}
    <ul class="badges">
      {#if device.held}
        <li class="chip-tag warn" data-testid="held-badge">{heldText(device.held_until)}</li>
      {/if}
      {#if device.release_pending}
        <li class="chip-tag warn" data-testid="release-pending">
          {device.release_due
            ? t('card.release_pending_at', { time: tTime(new Date(device.release_due)) })
            : t('card.release_pending')}
        </li>
      {/if}
      {#if device.paused}
        <li class="chip-tag warn" data-testid="paused">
          {t('card.paused', { reason: tDynamic(device.paused.key, device.paused.args) })}
        </li>
      {/if}
    </ul>
  {/if}
  {#if device.held}
    <button
      type="button"
      class="btn small secondary resume"
      disabled={busy}
      data-testid="resume-now"
      onclick={() => void control.override(device, { action: 'resume' })}
      >{t('card.resume_now')}</button
    >
  {/if}

  <div class="modes" role="group" aria-labelledby="{uid}-modes">
    <span id="{uid}-modes" class="label">{t('card.mode', { name: device.name })}</span>
    <div class="seg">
      {#each MODE_ORDER as m (m)}
        <button
          type="button"
          class="seg-btn"
          aria-pressed={device.device_mode === m}
          disabled={busy}
          data-testid="mode-{m}"
          onclick={() => choose(m)}>{t(MODE_LABEL[m])}</button
        >
      {/each}
    </div>
    {#if limitedByGlobal}
      <p class="hint" data-testid="mode-limited">
        {t('card.mode_limited', {
          effective: t(MODE_LABEL[device.effective_mode]),
          global: t(MODE_LABEL[device.global_mode]),
        })}
      </p>
    {/if}
  </div>

  {#if error}
    <p class="err" role="alert" data-testid="card-error">
      {tDynamic(error, undefined, 'api.error.unknown')}
    </p>
  {/if}

  <details class="why" data-testid="why">
    <summary>{t('card.why')}</summary>
    {#if device.reasons.length > 0}
      <ReasonList reasons={device.reasons} />
    {:else}
      <p class="hint">{t('card.why_none')}</p>
    {/if}
    {#if device.last_decision}
      {@const d = device.last_decision}
      <p class="last">
        <strong>{t('card.last_decision')}</strong>
        <time datetime={d.ts}>{tTime(new Date(d.ts))}</time>
        {t(KIND_LABEL[d.kind] ?? 'reason.unknown')}
      </p>
      <ReasonList reasons={d.reasons} />
    {/if}
  </details>

  <details class="manual">
    <summary>{t('card.manual')}</summary>
    <OverrideForm {device} />
  </details>
</article>

<ConfirmDialog
  open={pending !== null}
  title={t('card.auto_title', { name: device.name })}
  confirmLabel={t('card.auto_confirm')}
  danger
  onconfirm={() => void confirmAutomatic()}
  oncancel={() => (pending = null)}
  testid="auto-dialog-{device.device_id}"
>
  <p>{t('card.auto_text', { name: device.name })}</p>
  <p>{t('card.auto_global', { global: t(MODE_LABEL[device.global_mode]) })}</p>
</ConfirmDialog>

<style>
  .dev {
    display: grid;
    gap: 12px;
    align-content: start;
    min-width: 0;
  }
  header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
  }
  h3 {
    overflow-wrap: anywhere;
  }
  .facts {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(9rem, 1fr));
    gap: 8px 16px;
    margin: 0;
  }
  .facts div {
    min-width: 0;
  }
  dt {
    color: var(--secondary-text-color);
    font-size: 0.875rem;
  }
  dd {
    margin: 0;
    font-weight: 600;
    font-variant-numeric: tabular-nums;
  }
  .badges {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
  }
  .resume {
    justify-self: start;
  }
  .label {
    display: block;
    font-weight: 600;
    margin-bottom: 6px;
  }
  .seg {
    display: flex;
    border: 1px solid var(--primary-color);
    border-radius: 22px;
    overflow: hidden;
  }
  .seg-btn {
    flex: 1 1 0;
    min-height: 44px;
    padding: 0 6px;
    font: inherit;
    font-size: 0.875rem;
    border: 0;
    border-inline-start: 1px solid var(--primary-color);
    background: transparent;
    color: var(--primary-color);
    cursor: pointer;
  }
  .seg-btn:first-child {
    border-inline-start: 0;
  }
  .seg-btn[aria-pressed='true'] {
    background: var(--primary-color);
    color: var(--text-primary-color);
    font-weight: 700;
  }
  .seg-btn:disabled {
    opacity: 0.6;
    cursor: not-allowed;
  }
  .modes .hint {
    margin-top: 6px;
  }
  .err {
    margin: 0;
    color: var(--error-color);
  }
  summary {
    cursor: pointer;
    min-height: 32px;
    display: flex;
    align-items: center;
    font-weight: 600;
    color: var(--primary-color);
  }
  details > :global(*:not(summary)) {
    margin-top: 8px;
  }
  .last {
    margin: 8px 0 4px;
  }
</style>
