<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import type { BatteryAction, BatteryControlState, OperatingMode } from '../api/types';
  import { MODE_KEY, MODE_ORDER, MODE_SHORT_KEY } from '../control/present';
  import { activityOf, clampPercent, type Activity } from '../flow';
  import { t, tDynamic, tPercent, tPower, tTime, type MessageKey } from '../i18n/index.svelte';
  import { BATTERY_SCOPE, control } from '../stores/control.svelte';
  import Card from '../ui/Card.svelte';
  import CardFooter from '../ui/CardFooter.svelte';
  import Chip from '../ui/Chip.svelte';
  import ToggleGroup from '../ui/ToggleGroup.svelte';
  import ConfirmDialog from './ConfirmDialog.svelte';
  import ReasonList from './ReasonList.svelte';

  interface Props {
    battery: BatteryControlState;
    /** Live state of charge and power of the battery (null = not known). */
    socPct?: number | null;
    powerW?: number | null;
  }
  let { battery, socPct = null, powerW = null }: Props = $props();

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
  const ACTIVITY: Partial<Record<Activity, MessageKey>> = {
    idle: 'activity.idle',
    charging: 'activity.charging',
    discharging: 'activity.discharging',
  };

  const busy = $derived(control.busy[BATTERY_SCOPE] ?? false);
  const error = $derived(control.errors[BATTERY_SCOPE] ?? null);
  const limitedByGlobal = $derived(battery.effective_mode !== battery.battery_mode);
  const modeOptions = $derived(
    MODE_ORDER.map((m) => ({ value: m, label: t(MODE_SHORT_KEY[m]), name: t(MODE_KEY[m]) })),
  );
  const activity = $derived(
    powerW == null
      ? null
      : t(ACTIVITY[activityOf('battery', { value: powerW, quality: 'ok' })] ?? 'activity.idle'),
  );

  /** The limits of an action as rows; unset fields are the adapter's own settings. */
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
  const showHeld = $derived(battery.under_control && held.length > 0);
  const showWanted = $derived(wanted.length > 0 && !battery.under_control);
  const hasLimits = $derived(showHeld || limits.length > 0 || showWanted);

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

  const footerItems = $derived([
    ...(hasLimits
      ? [
          {
            id: 'limits',
            label: showHeld ? t('bcard.held') : t('bcard.limits'),
            panel: limitsPanel,
          },
        ]
      : []),
    { id: 'why', label: t('card.why'), testid: 'battery-why', panel: whyPanel },
  ]);
</script>

{#snippet rows(list: { key: MessageKey; value: string }[])}
  <ul class="rows">
    {#each list as r (r.key)}<li><span>{t(r.key)}</span> <strong>{r.value}</strong></li>{/each}
  </ul>
{/snippet}

{#snippet limitsPanel()}
  {#if showHeld}
    <div data-testid="battery-held">{@render rows(held)}</div>
  {:else if limits.length > 0}
    <div data-testid="battery-limits">{@render rows(limits)}</div>
  {/if}
  {#if showWanted}
    <div data-testid="battery-wanted">
      <p class="kicker">{t('bcard.wanted')}</p>
      {@render rows(wanted)}
    </div>
  {/if}
{/snippet}

{#snippet whyPanel()}
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
{/snippet}

<Card
  title={t('battery.title')}
  icon="battery"
  testid="battery-card"
  data-mode={battery.effective_mode}
>
  {#snippet status()}
    <Chip
      tone={battery.effective_mode === 'automatic' ? 'warn' : 'neutral'}
      testid="battery-effective-mode">{t(MODE_KEY[battery.effective_mode])}</Chip
    >
  {/snippet}

  <div class="gauge">
    <div class="soc">
      <span class="sr-only">{t('bcard.soc')}</span>
      <span class="big" data-testid="battery-soc"
        >{socPct == null ? t('site.unknown') : tPercent(socPct)}</span
      >
      <span class="power" data-testid="battery-power">
        {powerW == null ? '' : tPower(Math.abs(powerW))}
        {activity ?? ''}
      </span>
    </div>
    <span class="bar" aria-hidden="true"
      ><span class="fill" style:width="{socPct == null ? 0 : clampPercent(socPct)}%"></span></span
    >
  </div>

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

  <ToggleGroup
    label={t('bcard.mode')}
    options={modeOptions}
    value={battery.battery_mode}
    disabled={busy}
    onselect={choose}
    testid="battery-mode"
  />
  {#if limitedByGlobal}
    <p class="hint" data-testid="battery-mode-limited">
      {t('card.mode_limited', {
        effective: t(MODE_KEY[battery.effective_mode]),
        global: t(MODE_KEY[battery.global_mode]),
      })}
    </p>
  {/if}
  {#if battery.mode_capped}
    <p class="notice info" data-testid="battery-cap-note">{t('bcard.cap_fallback')}</p>
  {/if}

  {#if error}
    <p class="err" role="alert" data-testid="battery-error">
      {tDynamic(error, undefined, 'api.error.unknown')}
    </p>
  {/if}

  {#snippet footer()}
    <CardFooter items={footerItems} />
  {/snippet}
</Card>

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
  <p>{t('card.auto_global', { global: t(MODE_KEY[battery.global_mode]) })}</p>
</ConfirmDialog>

<style>
  .gauge {
    display: grid;
    gap: var(--sc-space-2);
  }
  .soc {
    display: flex;
    flex-wrap: wrap;
    align-items: baseline;
    justify-content: space-between;
    gap: var(--sc-space-1) var(--sc-space-3);
  }
  .big {
    font-size: var(--sc-font-2xl);
    font-weight: var(--sc-weight-strong);
    line-height: var(--sc-leading-tight);
    font-variant-numeric: tabular-nums;
  }
  .power {
    color: var(--sc-text-muted);
    font-variant-numeric: tabular-nums;
  }
  .bar {
    display: block;
    height: 10px;
    border-radius: var(--sc-radius-pill);
    background: var(--sc-surface-sunken);
    box-shadow: inset 0 0 0 1px var(--sc-outline);
    overflow: hidden;
  }
  .fill {
    display: block;
    height: 100%;
    border-radius: inherit;
    background: var(--sc-energy-bat-in);
    transition: width var(--sc-motion-normal) ease;
  }
  .badges {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-wrap: wrap;
    gap: var(--sc-space-1);
  }
  .rows {
    display: grid;
    gap: var(--sc-space-1);
    margin: 0;
    padding: 0;
    list-style: none;
  }
  .rows li {
    display: flex;
    justify-content: space-between;
    gap: var(--sc-space-3);
  }
  .rows strong {
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
  }
  .err {
    margin: 0;
    color: var(--sc-error);
  }
  .last {
    margin: 0;
  }
</style>
