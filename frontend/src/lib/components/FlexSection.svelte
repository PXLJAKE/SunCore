<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import type { DeviceConfig, DeviceControlState, EvMode } from '../api/types';
  import {
    defaultDeadline,
    EV_MODES,
    evPlug,
    evTarget,
    flexKind,
    jobOpen,
    liftK,
    parseDeadline,
    sgState,
    startsOnRelease,
    targetToForm,
    thermalView,
    toLocalInput,
    type TargetError,
    type TargetKind,
    type DeadlineError,
  } from '../flex/present';
  import { t, tDateTime, tDynamic, tNumber, tPercent, type MessageKey } from '../i18n/index.svelte';
  import { flex } from '../stores/flex.svelte';
  import { live } from '../stores/live.svelte';
  import ConfirmDialog from './ConfirmDialog.svelte';

  interface Props {
    device: DeviceControlState;
    config: DeviceConfig | undefined;
    /** Reference time (injected for tests). */
    now?: Date;
  }
  let { device, config, now }: Props = $props();

  const uid = $props.id();
  const kind = $derived(flexKind(config));
  const id = $derived(device.device_id);
  const view = $derived(flex.views[id]);
  const busy = $derived(flex.busy[id] ?? false);
  const error = $derived(flex.errors[id] ?? null);
  // The server judges "in the future" and "within 7 days" by its own clock (a demo or a skewed
  // browser clock differs), so defaults and checks use the time of the last control state.
  const clock = (): Date => {
    if (now) return now;
    const ts = live.control?.ts;
    const server = ts ? new Date(ts) : null;
    return server && !Number.isNaN(server.getTime()) ? server : new Date();
  };

  const MODE_LABEL: Record<EvMode, MessageKey> = {
    off: 'ev.mode.off',
    now: 'ev.mode.now',
    min_pv: 'ev.mode.min_pv',
    pv: 'ev.mode.pv',
    target: 'ev.mode.target',
  };
  const PLUG_LABEL = {
    unplugged: 'ev.plug.unplugged',
    plugged: 'ev.plug.plugged',
    unknown: 'ev.plug.unknown',
  } as const satisfies Record<string, MessageKey>;
  const TARGET_ERROR: Record<TargetError | DeadlineError, MessageKey> = {
    invalid: 'flex.error.by_invalid',
    past: 'flex.error.by_past',
    value: 'ev.error.value',
  };

  // Only devices with a flex section poll their view (every 10 s, plus after each action).
  $effect(() => {
    if (kind !== 'ev' && kind !== 'program') return;
    const deviceId = id;
    void flex.load(deviceId);
    const timer = setInterval(() => void flex.load(deviceId), 10_000);
    return () => clearInterval(timer);
  });

  // --- EV ---------------------------------------------------------------------------------
  const mode = $derived<EvMode>(view?.ev?.mode ?? config?.ev?.default_mode ?? 'pv');
  const stored = $derived(targetToForm(view?.ev?.target));
  let targetOpen = $state(false);
  let targetKind = $state<TargetKind>('soc');
  let targetValue = $state<number | null>(80);
  let targetBy = $state('');
  let targetError = $state<MessageKey | null>(null);
  let seeded = false;
  $effect(() => {
    if (seeded || !kind) return;
    seeded = true;
    const s = stored;
    if (s) {
      targetKind = s.kind;
      targetValue = s.value;
      targetBy = s.by;
    } else {
      targetBy = toLocalInput(defaultDeadline(clock()));
    }
  });

  function chooseMode(m: EvMode): void {
    if (busy) return;
    if (m === 'target') {
      targetOpen = true;
      return;
    }
    targetOpen = false;
    targetError = null;
    void flex.setEv(id, m, null);
  }

  async function applyTarget(e: Event): Promise<void> {
    e.preventDefault();
    const r = evTarget(targetKind, targetValue, targetBy, clock());
    targetError = r.ok ? null : TARGET_ERROR[r.error];
    if (!r.ok) return;
    if (await flex.setEv(id, 'target', r.target)) targetOpen = false;
  }

  const plug = $derived(evPlug(device.state));
  const vehicleSoc = $derived(device.state?.soc_pct ?? null);

  // --- heat pump --------------------------------------------------------------------------
  const sg = $derived(sgState(device.state?.step));
  const lift = $derived(liftK(device));
  const thermal = $derived(thermalView(config?.thermal));

  // --- program ----------------------------------------------------------------------------
  const job = $derived(view?.job ?? null);
  const open = $derived(jobOpen(job));
  let deadline = $state('');
  let deadlineError = $state<MessageKey | null>(null);
  let cancelAsk = $state(false);
  $effect(() => {
    if (kind === 'program' && deadline === '') deadline = toLocalInput(defaultDeadline(clock()));
  });
  const plugStart = $derived(startsOnRelease(config));

  async function arm(e: Event): Promise<void> {
    e.preventDefault();
    const r = parseDeadline(deadline, clock());
    deadlineError = r.ok ? null : TARGET_ERROR[r.error];
    if (r.ok) await flex.arm(id, r.iso);
  }

  async function confirmCancel(): Promise<void> {
    cancelAsk = false;
    await flex.cancel(id);
  }
</script>

{#if kind === 'ev'}
  <section class="flex" aria-labelledby="{uid}-ev" data-testid="ev-section-{id}">
    <h4 id="{uid}-ev">{t('ev.title')}</h4>
    <dl class="facts">
      <div>
        <dt>{t('ev.plug')}</dt>
        <dd data-testid="ev-plug">{t(PLUG_LABEL[plug])}</dd>
      </div>
      <div>
        <dt>{t('ev.phases')}</dt>
        <dd data-testid="ev-phases">
          {device.state?.phases != null
            ? t('ev.phases_value', { count: device.state.phases })
            : t('ev.unknown')}
        </dd>
      </div>
      <div>
        <dt>{t('ev.vehicle_soc')}</dt>
        <dd data-testid="ev-soc">
          {vehicleSoc != null ? tPercent(vehicleSoc) : t('ev.unknown')}
        </dd>
      </div>
    </dl>
    <div role="group" aria-label={t('ev.mode', { name: device.name })}>
      <div class="seg">
        {#each EV_MODES as m (m)}
          <button
            type="button"
            class="seg-btn"
            aria-pressed={mode === m}
            disabled={busy}
            data-testid="ev-mode-{m}"
            onclick={() => chooseMode(m)}>{t(MODE_LABEL[m])}</button
          >
        {/each}
      </div>
      <p class="hint">{t(`ev.mode.${mode}_hint`)}</p>
    </div>
    {#if mode === 'target' && stored && !targetOpen}
      <p data-testid="ev-target">
        {stored.kind === 'soc'
          ? t('ev.target_soc', {
              value: tPercent(stored.value ?? 0),
              by: tDateTime(new Date(view?.ev?.target?.by ?? '')),
            })
          : t('ev.target_energy', {
              value: tNumber(stored.value ?? 0, { maximumFractionDigits: 1 }),
              by: tDateTime(new Date(view?.ev?.target?.by ?? '')),
            })}
        <button type="button" class="btn small secondary" onclick={() => (targetOpen = true)}
          >{t('ev.target_change')}</button
        >
      </p>
    {/if}
    {#if targetOpen}
      <form onsubmit={applyTarget} novalidate data-testid="ev-target-form" class="stack">
        <div class="radios" role="radiogroup" aria-label={t('ev.target_kind')}>
          <label class="radio">
            <input type="radio" name="{uid}-tk" value="soc" bind:group={targetKind} />
            {t('ev.target_kind.soc')}
          </label>
          <label class="radio">
            <input type="radio" name="{uid}-tk" value="energy" bind:group={targetKind} />
            {t('ev.target_kind.energy')}
          </label>
        </div>
        <div class="field">
          <label for="{uid}-tv">
            {targetKind === 'soc' ? t('ev.target_value_soc') : t('ev.target_value_energy')}
          </label>
          <input
            id="{uid}-tv"
            class="input"
            type="number"
            inputmode="decimal"
            min="1"
            step="any"
            bind:value={targetValue}
            data-testid="ev-target-value"
          />
        </div>
        <div class="field">
          <label for="{uid}-tb">{t('ev.target_by')}</label>
          <input
            id="{uid}-tb"
            class="input"
            type="datetime-local"
            bind:value={targetBy}
            data-testid="ev-target-by"
          />
        </div>
        {#if targetError}<p class="err" role="alert">{t(targetError)}</p>{/if}
        <div class="row">
          <button type="submit" class="btn small" disabled={busy} data-testid="ev-target-apply"
            >{t('ev.target_apply')}</button
          >
          <button type="button" class="btn small secondary" onclick={() => (targetOpen = false)}
            >{t('action.cancel')}</button
          >
        </div>
      </form>
    {/if}
    {#if error}
      <p class="err" role="alert" data-testid="flex-error">
        {tDynamic(error, undefined, 'api.error.unknown')}
      </p>
    {/if}
  </section>
{:else if kind === 'heat_pump'}
  <section class="flex" aria-labelledby="{uid}-hp" data-testid="hp-section-{id}">
    <h4 id="{uid}-hp">{t('hp.title')}</h4>
    <dl class="facts">
      {#if config?.control.kind === 'sg_ready'}
        <div>
          <dt>{t('hp.sg')}</dt>
          <dd data-testid="hp-sg">
            {sg === null ? t('hp.unknown') : t(`hp.sg.${sg}`)}
          </dd>
        </div>
      {/if}
      {#if config?.control.kind === 'setpoint'}
        <div>
          <dt>{t('hp.lift')}</dt>
          <dd data-testid="hp-lift">
            {lift === null
              ? t('hp.unknown')
              : t('hp.lift_value', { value: tNumber(lift, { maximumFractionDigits: 1 }) })}
          </dd>
        </div>
      {/if}
      <div>
        <dt>{t('hp.cop')}</dt>
        <dd data-testid="hp-cop">
          {#if thermal.cop}
            {t('hp.cop_range', {
              min: tNumber(thermal.cop.min, { maximumFractionDigits: 1 }),
              max: tNumber(thermal.cop.max, { maximumFractionDigits: 1 }),
            })}
            {#if thermal.cop.assumed}<span class="chip-tag warn">{t('hp.cop_assumed')}</span>{/if}
          {:else}
            {t('hp.unknown')}
          {/if}
        </dd>
      </div>
      <div>
        <dt>{t('hp.model')}</dt>
        <dd data-testid="hp-model">
          {thermal.nrmse === null
            ? t('hp.model_none')
            : t('hp.model_learned', { error: tPercent(thermal.nrmse * 100) })}
        </dd>
      </div>
    </dl>
  </section>
{:else if kind === 'program'}
  <section class="flex" aria-labelledby="{uid}-pg" data-testid="program-section-{id}">
    <h4 id="{uid}-pg">{t('program.title')}</h4>
    {#if open && job}
      <dl class="facts">
        <div>
          <dt>{t('program.job')}</dt>
          <dd data-testid="program-job-state">{t(`program.state.${job.state}`)}</dd>
        </div>
        <div>
          <dt>{t('program.finished_by')}</dt>
          <dd data-testid="program-deadline">{tDateTime(new Date(job.deadline))}</dd>
        </div>
        <div>
          <dt>{t('program.planned_start')}</dt>
          <dd data-testid="program-planned">
            {job.planned_start ? tDateTime(new Date(job.planned_start)) : t('program.not_planned')}
          </dd>
        </div>
      </dl>
      {#if job.state !== 'running'}
        <button
          type="button"
          class="btn small secondary"
          disabled={busy}
          data-testid="program-cancel"
          onclick={() => (cancelAsk = true)}>{t('program.cancel')}</button
        >
      {:else}
        <p class="hint">{t('program.running_hint')}</p>
      {/if}
    {:else}
      <p class="hint">{t('program.idle_hint')}</p>
      <form onsubmit={arm} novalidate class="stack" data-testid="program-arm-form">
        <div class="field">
          <label for="{uid}-dl">{t('program.finished_by')}</label>
          <input
            id="{uid}-dl"
            class="input"
            type="datetime-local"
            bind:value={deadline}
            data-testid="program-deadline-input"
          />
        </div>
        {#if deadlineError}<p class="err" role="alert">{t(deadlineError)}</p>{/if}
        <div>
          <button type="submit" class="btn small" disabled={busy} data-testid="program-arm"
            >{t('program.arm')}</button
          >
        </div>
      </form>
    {/if}
    {#if error}
      <p class="err" role="alert" data-testid="flex-error">
        {tDynamic(error, undefined, 'api.error.unknown')}
      </p>
    {/if}
  </section>

  <ConfirmDialog
    open={cancelAsk}
    title={t('program.cancel_title', { name: device.name })}
    confirmLabel={t('program.cancel_confirm')}
    danger={plugStart}
    onconfirm={() => void confirmCancel()}
    oncancel={() => (cancelAsk = false)}
    testid="program-cancel-dialog-{id}"
  >
    <p>{t('program.cancel_text')}</p>
    {#if plugStart}
      <p class="notice warn" data-testid="program-cancel-warning">
        {t('program.plug_warning', { name: device.name })}
      </p>
    {/if}
  </ConfirmDialog>
{/if}

<style>
  .flex {
    display: grid;
    gap: 10px;
    padding-top: 10px;
    border-top: 1px solid var(--divider-color);
    min-width: 0;
  }
  h4 {
    margin: 0;
  }
  .facts {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(8rem, 1fr));
    gap: 8px 16px;
    margin: 0;
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
  .seg {
    display: flex;
    flex-wrap: wrap;
    border: 1px solid var(--primary-color);
    border-radius: 22px;
    overflow: hidden;
  }
  .seg-btn {
    flex: 1 1 0;
    min-width: 4.5rem;
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
  .row {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
  }
  .err {
    margin: 0;
    color: var(--error-color);
    font-size: 0.875rem;
  }
</style>
