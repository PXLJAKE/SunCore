<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import type { DeviceConfig, DeviceControlState, OperatingMode } from '../api/types';
  import { startsOnRelease } from '../flex/present';
  import {
    holdDay,
    MODE_KEY,
    MODE_ORDER,
    MODE_SHORT_KEY,
    qualityKey,
    stateView,
    virtualDraw,
  } from '../control/present';
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
  import FlexSection from './FlexSection.svelte';
  import OverrideForm from './OverrideForm.svelte';
  import ReasonList from './ReasonList.svelte';
  import Card from '../ui/Card.svelte';
  import CardFooter from '../ui/CardFooter.svelte';
  import Chip from '../ui/Chip.svelte';
  import ToggleGroup from '../ui/ToggleGroup.svelte';

  interface Props {
    device: DeviceControlState;
    /** Reference time for "held until tomorrow" (injected for tests). */
    now?: Date;
    /** Unit of an adjustable load's value (W, A, %) from the device configuration. */
    unit?: string;
    /** The stored device (EV, program and heat pump sections depend on it). */
    config?: DeviceConfig;
  }
  let { device, now, unit, config }: Props = $props();

  const modeOptions = $derived(
    MODE_ORDER.map((m) => ({ value: m, label: t(MODE_SHORT_KEY[m]), name: t(MODE_KEY[m]) })),
  );
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
  const stateBad = $derived(qualityKey(device.state?.quality));
  const powerBad = $derived(qualityKey(estimate?.quality));
  const virtualW = $derived(virtualDraw(device));
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
        return unit
          ? t('card.state.level_unit', { value: num(view.value), unit })
          : t('card.state.level', { value: num(view.value) });
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

  // --- "back to normal" of a program started through a plug ---------------------------------
  // Releasing such a device switches the plug ON: the machine starts at once. Ask first.
  const plugStart = $derived(startsOnRelease(config));
  let resumeAsk = $state(false);

  function requestResume(): void {
    if (plugStart) resumeAsk = true;
    else void control.override(device, { action: 'resume' });
  }

  async function confirmResume(): Promise<void> {
    resumeAsk = false;
    await control.override(device, { action: 'resume' });
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

{#snippet whyPanel()}
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
{/snippet}

{#snippet manualPanel()}
  <OverrideForm {device} onresume={requestResume} />
{/snippet}

<Card
  article
  id="device-card-{device.device_id}"
  title={device.name}
  testid="device-{device.device_id}"
  data-mode={device.effective_mode}
  data-held={device.held ? 'true' : undefined}
>
  {#snippet status()}
    <Chip tone={device.effective_mode === 'automatic' ? 'warn' : 'neutral'} testid="effective-mode"
      >{t(MODE_KEY[device.effective_mode])}</Chip
    >
  {/snippet}

  <dl class="facts">
    <div>
      <dt>{t('card.state')}</dt>
      <dd data-testid="state">
        {stateText()}
        {#if stateBad}<span class="chip-tag warn" data-testid="state-quality">{t(stateBad)}</span
          >{/if}
      </dd>
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
        {#if powerBad}<span class="chip-tag warn" data-testid="power-quality">{t(powerBad)}</span
          >{/if}
      </dd>
      {#if virtualW > 0}
        <dd class="virtual" data-testid="would-run">
          {t('card.would_run', { power: tPower(device.committed_w ?? virtualW) })}
        </dd>
      {/if}
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
      onclick={requestResume}>{t('card.resume_now')}</button
    >
  {/if}

  <FlexSection {device} {config} {now} />

  <ToggleGroup
    label={t('card.mode', { name: device.name })}
    options={modeOptions}
    value={device.device_mode}
    disabled={busy}
    onselect={choose}
    testid="mode"
  />
  {#if limitedByGlobal}
    <p class="hint" data-testid="mode-limited">
      {t('card.mode_limited', {
        effective: t(MODE_KEY[device.effective_mode]),
        global: t(MODE_KEY[device.global_mode]),
      })}
    </p>
  {/if}

  {#if error}
    <p class="err" role="alert" data-testid="card-error">
      {tDynamic(error, undefined, 'api.error.unknown')}
    </p>
  {/if}

  {#snippet footer()}
    <CardFooter
      items={[
        { id: 'why', label: t('card.why'), testid: 'why', panel: whyPanel },
        { id: 'manual', label: t('card.manual'), panel: manualPanel },
      ]}
    />
  {/snippet}
</Card>

<ConfirmDialog
  open={resumeAsk}
  title={t('card.resume_title', { name: device.name })}
  confirmLabel={t('card.resume_confirm')}
  danger
  onconfirm={() => void confirmResume()}
  oncancel={() => (resumeAsk = false)}
  testid="resume-dialog-{device.device_id}"
>
  <p>{t('card.resume_text', { name: device.name })}</p>
  <p class="notice warn" data-testid="resume-warning">
    {t('program.plug_warning', { name: device.name })}
  </p>
</ConfirmDialog>

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
  <p>{t('card.auto_global', { global: t(MODE_KEY[device.global_mode]) })}</p>
</ConfirmDialog>

<style>
  .virtual {
    font-weight: var(--sc-weight-regular);
    font-size: var(--sc-font-xs);
    color: var(--sc-text-muted);
  }
  .badges {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-wrap: wrap;
    gap: var(--sc-space-1);
  }
  .resume {
    align-self: flex-start;
  }
  .err {
    margin: 0;
    color: var(--sc-error);
  }
  .last {
    margin: 0;
  }
</style>
