<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import type { BatteryAction, BatteryControlState, OperatingMode } from '../api/types';
  import { MODE_ORDER } from '../control/present';
  import { t, tDynamic, tPercent, tPower, tTime, type MessageKey } from '../i18n/index.svelte';
  import { BATTERY_SCOPE, control } from '../stores/control.svelte';
  import ConfirmDialog from './ConfirmDialog.svelte';
  import ReasonList from './ReasonList.svelte';

  interface Props {
    battery: BatteryControlState;
    /** Live state of charge and power of the battery (null = not known). */
    socPct?: number | null;
    powerW?: number | null;
  }
  let { battery, socPct = null, powerW = null }: Props = $props();

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
  const CAPABILITY: Record<string, MessageKey> = {
    min_soc: 'bcard.cap.min_soc',
    charge_limit: 'bcard.cap.charge_limit',
    discharge_limit: 'bcard.cap.discharge_limit',
    grid_charge: 'bcard.cap.grid_charge',
    hardware_fallback: 'bcard.cap.hardware_fallback',
  };

  const busy = $derived(control.busy[BATTERY_SCOPE] ?? false);
  const error = $derived(control.errors[BATTERY_SCOPE] ?? null);
  const limitedByGlobal = $derived(battery.effective_mode !== battery.battery_mode);

  /** The limits of an action as list items; unset fields are the adapter's own settings. */
  function limitRows(a: BatteryAction | null): { key: MessageKey; value: string }[] {
    if (!a) return [];
    const rows: { key: MessageKey; value: string }[] = [];
    if (a.min_soc_pct != null)
      rows.push({ key: 'bcard.limit.min_soc', value: tPercent(a.min_soc_pct) });
    if (a.max_charge_w != null)
      rows.push({ key: 'bcard.limit.max_charge', value: tPower(a.max_charge_w) });
    if (a.max_discharge_w != null) {
      rows.push({ key: 'bcard.limit.max_discharge', value: tPower(a.max_discharge_w) });
    }
    if (a.grid_charge_w != null)
      rows.push({ key: 'bcard.limit.grid_charge', value: tPower(a.grid_charge_w) });
    return rows;
  }

  const held = $derived(limitRows(battery.held));
  const limits = $derived(limitRows(battery.limits));
  const wanted = $derived(limitRows(battery.wanted));

  // --- mode switch ---------------------------------------------------------------------
  /** The token the user was shown when the dialog opened (sent as is: a re-mapped battery 409s). */
  let pending = $state<{ identity: string; mode: OperatingMode } | null>(null);

  function choose(mode: OperatingMode): void {
    if (mode === battery.battery_mode || busy) return;
    control.clearError(BATTERY_SCOPE);
    if (mode === 'shadow') {
      void control.setBatteryMode(mode, battery.identity);
      return;
    }
    // Writing to the inverter starts here: assisted and automatic both ask first.
    pending = { identity: battery.identity, mode };
  }

  async function confirm(): Promise<void> {
    if (!pending) return;
    const { mode, identity } = pending;
    pending = null;
    await control.setBatteryMode(mode, identity);
  }
</script>

<article
  class="card bat"
  aria-labelledby="{uid}-name"
  data-testid="battery-card"
  data-mode={battery.effective_mode}
>
  <header>
    <h3 id="{uid}-name">{t('battery.title')}</h3>
    <span
      class="chip-tag"
      class:warn={battery.effective_mode === 'automatic'}
      data-testid="battery-effective-mode">{t(MODE_LABEL[battery.effective_mode])}</span
    >
  </header>

  <dl class="facts">
    <div>
      <dt>{t('bcard.soc')}</dt>
      <dd data-testid="battery-soc">{socPct == null ? t('site.unknown') : tPercent(socPct)}</dd>
    </div>
    <div>
      <dt>{t('bcard.power')}</dt>
      <dd data-testid="battery-power">{powerW == null ? t('site.unknown') : tPower(powerW)}</dd>
    </div>
  </dl>

  <ul class="badges">
    <li class="chip-tag" class:warn={battery.under_control} data-testid="battery-control">
      {battery.under_control ? t('bcard.under_control') : t('bcard.own_settings')}
    </li>
    {#if battery.paused}
      <li class="chip-tag warn" data-testid="battery-paused">
        {t('bcard.paused', { reason: tDynamic(battery.paused.key, battery.paused.args) })}
      </li>
    {/if}
  </ul>

  {#if battery.under_control && held.length > 0}
    <div data-testid="battery-held">
      <p class="label">{t('bcard.held')}</p>
      <ul class="rows">
        {#each held as r (r.key)}<li>{t(r.key)}: <strong>{r.value}</strong></li>{/each}
      </ul>
    </div>
  {:else if limits.length > 0}
    <div data-testid="battery-limits">
      <p class="label">{t('bcard.limits')}</p>
      <ul class="rows">
        {#each limits as r (r.key)}<li>{t(r.key)}: <strong>{r.value}</strong></li>{/each}
      </ul>
    </div>
  {/if}
  {#if wanted.length > 0 && !battery.under_control}
    <div data-testid="battery-wanted">
      <p class="label">{t('bcard.wanted')}</p>
      <ul class="rows">
        {#each wanted as r (r.key)}<li>{t(r.key)}: <strong>{r.value}</strong></li>{/each}
      </ul>
    </div>
  {/if}

  <div class="modes" role="group" aria-labelledby="{uid}-modes">
    <span id="{uid}-modes" class="label">{t('bcard.mode')}</span>
    <div class="seg">
      {#each MODE_ORDER as m (m)}
        <button
          type="button"
          class="seg-btn"
          aria-pressed={battery.battery_mode === m}
          disabled={busy}
          data-testid="battery-mode-{m}"
          onclick={() => choose(m)}>{t(MODE_LABEL[m])}</button
        >
      {/each}
    </div>
    {#if limitedByGlobal}
      <p class="hint" data-testid="battery-mode-limited">
        {t('card.mode_limited', {
          effective: t(MODE_LABEL[battery.effective_mode]),
          global: t(MODE_LABEL[battery.global_mode]),
        })}
      </p>
    {/if}
    {#if battery.mode_capped}
      <p class="notice info" data-testid="battery-cap-note">
        {t('bcard.cap_fallback')}
      </p>
    {/if}
  </div>

  {#if error}
    <p class="err" role="alert" data-testid="battery-error">
      {tDynamic(error, undefined, 'api.error.unknown')}
    </p>
  {/if}

  <details class="why" data-testid="battery-why">
    <summary>{t('card.why')}</summary>
    {#if battery.reasons.length > 0}
      <ReasonList reasons={battery.reasons} />
    {:else}
      <p class="hint">{t('card.why_none')}</p>
    {/if}
    {#if battery.last_decision}
      {@const d = battery.last_decision}
      <p class="last" data-testid="battery-last">
        <strong>{t('card.last_decision')}</strong>
        <time datetime={d.ts}>{tTime(new Date(d.ts))}</time>
        {t(KIND_LABEL[d.kind] ?? 'reason.unknown')}
      </p>
      <ReasonList reasons={d.reasons} />
    {/if}
    <p class="hint">
      {t('bcard.adapter', { adapter: battery.adapter_template_id })}
      {#each battery.capabilities as c (c)}
        <span class="chip-tag">{CAPABILITY[c] ? t(CAPABILITY[c]) : c}</span>
      {/each}
    </p>
  </details>
</article>

<ConfirmDialog
  open={pending !== null}
  title={t('bcard.confirm_title')}
  confirmLabel={pending?.mode === 'automatic'
    ? t('bcard.confirm_automatic')
    : t('bcard.confirm_assisted')}
  danger={pending?.mode === 'automatic'}
  onconfirm={() => void confirm()}
  oncancel={() => (pending = null)}
  testid="battery-dialog"
>
  <p>
    {t(
      pending?.mode === 'automatic'
        ? 'bcard.confirm_text_automatic'
        : 'bcard.confirm_text_assisted',
    )}
  </p>
  <p>{t('bcard.confirm_reserve')}</p>
  {#if battery.mode_capped && pending?.mode === 'automatic'}
    <p>{t('bcard.confirm_capped')}</p>
  {/if}
  <p>{t('card.auto_global', { global: t(MODE_LABEL[battery.global_mode]) })}</p>
</ConfirmDialog>

<style>
  .bat {
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
  .badges {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
  }
  .label {
    display: block;
    font-weight: 600;
    margin: 0 0 6px;
  }
  .rows {
    margin: 0;
    padding-left: 20px;
    font-variant-numeric: tabular-nums;
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
  .modes .hint,
  .modes .notice {
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
