<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import type { DeviceControlState } from '../api/types';
  import {
    BOOST_MAX,
    BOOST_MIN,
    boostCommand,
    localDateTimeValue,
    pauseCommand,
    resumeCommand,
    type OverrideError,
  } from '../control/overrideForm';
  import { t, type MessageKey } from '../i18n/index.svelte';
  import { control } from '../stores/control.svelte';

  interface Props {
    device: Pick<DeviceControlState, 'device_id' | 'name' | 'held'>;
  }
  let { device }: Props = $props();

  const uid = $props.id();
  let minutes = $state<number | null>(60);
  let until = $state('');
  let boostError = $state<OverrideError | null>(null);
  let pauseError = $state<OverrideError | null>(null);
  const busy = $derived(control.busy[device.device_id] ?? false);

  const ERROR: Record<OverrideError, MessageKey> = {
    boost_minutes: 'override.error.boost_minutes',
    pause_invalid: 'override.error.pause_invalid',
    pause_past: 'override.error.pause_past',
  };

  async function boost(e: Event): Promise<void> {
    e.preventDefault();
    const r = boostCommand(minutes);
    boostError = r.ok ? null : r.error;
    if (r.ok) await control.override(device, r.command);
  }

  async function pause(e: Event): Promise<void> {
    e.preventDefault();
    const r = pauseCommand(until);
    pauseError = r.ok ? null : r.error;
    if (r.ok && (await control.override(device, r.command))) until = '';
  }
</script>

<div class="override" data-testid="override-{device.device_id}">
  <form onsubmit={boost} novalidate>
    <div class="field">
      <label for="{uid}-min">{t('override.boost_label')}</label>
      <div class="row">
        <input
          id="{uid}-min"
          class="input"
          type="number"
          inputmode="numeric"
          min={BOOST_MIN}
          max={BOOST_MAX}
          step="1"
          bind:value={minutes}
          aria-invalid={boostError ? 'true' : undefined}
          aria-describedby="{uid}-min-hint"
        />
        <button type="submit" class="btn small" disabled={busy} data-testid="override-boost"
          >{t('override.boost')}</button
        >
      </div>
      <p id="{uid}-min-hint" class="hint">{t('override.boost_hint')}</p>
      {#if boostError}<p class="err" role="alert">{t(ERROR[boostError])}</p>{/if}
    </div>
  </form>

  <form onsubmit={pause} novalidate>
    <div class="field">
      <label for="{uid}-until">{t('override.pause_label')}</label>
      <div class="row">
        <input
          id="{uid}-until"
          class="input"
          type="datetime-local"
          min={localDateTimeValue(new Date())}
          bind:value={until}
          aria-invalid={pauseError ? 'true' : undefined}
          aria-describedby="{uid}-until-hint"
        />
        <button type="submit" class="btn small" disabled={busy} data-testid="override-pause"
          >{t('override.pause')}</button
        >
      </div>
      <p id="{uid}-until-hint" class="hint">{t('override.pause_hint')}</p>
      {#if pauseError}<p class="err" role="alert">{t(ERROR[pauseError])}</p>{/if}
    </div>
  </form>

  <button
    type="button"
    class="btn small secondary"
    disabled={busy || !device.held}
    data-testid="override-resume"
    onclick={() => void control.override(device, resumeCommand())}>{t('override.resume')}</button
  >
</div>

<style>
  .override {
    display: grid;
    gap: 12px;
    justify-items: start;
  }
  form {
    width: 100%;
  }
  .row {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
  }
  .row .input {
    flex: 1 1 10rem;
    width: auto;
  }
  .err {
    margin: 0;
    color: var(--error-color);
    font-size: 0.875rem;
  }
</style>
