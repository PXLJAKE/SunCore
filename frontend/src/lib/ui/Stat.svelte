<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import { t } from '../i18n/index.svelte';

  export type EnergyTone =
    'pv' | 'grid-in' | 'grid-out' | 'bat-in' | 'bat-out' | 'house' | 'device';

  interface Props {
    label: string;
    /** The formatted value (numbers are formatted by the caller through the ICU helpers). */
    value: string;
    /** Unit shown smaller after the value, when the value does not carry it already. */
    unit?: string;
    /** One short line under the value (state, comparison). */
    hint?: string;
    /** Colour marker of the energy palette. */
    tone?: EnergyTone;
    /**
     * Quality of the value: `stale` (too old) is shown muted, `mismatch` (the unit does not fit the
     * role, the UnitBadge rule) gets a warning mark. Both are spelled out for screen readers.
     */
    quality?: 'ok' | 'stale' | 'mismatch';
    size?: 'regular' | 'large';
    testid?: string;
  }
  let {
    label,
    value,
    unit,
    hint,
    tone,
    quality = 'ok',
    size = 'regular',
    testid,
  }: Props = $props();
</script>

<dl class="stat {size}" class:stale={quality === 'stale'} data-testid={testid}>
  <dt class="label">
    {#if tone}<span class="dot" style:background="var(--sc-energy-{tone})" aria-hidden="true"
      ></span>{/if}
    {label}
  </dt>
  <dd class="value">
    {value}{#if unit}<span class="unit">{unit}</span>{/if}
    {#if quality === 'mismatch'}<span class="mark" aria-hidden="true">!</span><span class="sr-only"
        >{t('unit.mismatch')}</span
      >{/if}
    {#if quality === 'stale'}<span class="sr-only">{t('ui.stat.stale')}</span>{/if}
  </dd>
  {#if hint}<dd class="hint">{hint}</dd>{/if}
</dl>

<style>
  .stat {
    display: grid;
    align-content: start;
    gap: 2px;
    min-width: 0;
    margin: 0;
  }
  dd {
    margin: 0;
  }
  .label {
    display: flex;
    align-items: center;
    gap: var(--sc-space-2);
    font-size: var(--sc-font-sm);
    color: var(--sc-text-muted);
  }
  .dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    flex: none;
  }
  .value {
    font-size: var(--sc-font-lg);
    font-weight: var(--sc-weight-strong);
    font-variant-numeric: tabular-nums;
    line-height: var(--sc-leading-tight);
    overflow-wrap: anywhere;
  }
  .large .value {
    font-size: var(--sc-font-2xl);
    letter-spacing: -0.01em;
  }
  .unit {
    margin-inline-start: 0.2em;
    font-size: 0.7em;
    font-weight: var(--sc-weight-regular);
    color: var(--sc-text-muted);
  }
  .mark {
    margin-inline-start: var(--sc-space-1);
    color: var(--sc-warn);
  }
  .stale .value {
    color: var(--sc-text-muted);
  }
  .hint {
    font-size: var(--sc-font-sm);
    color: var(--sc-text-muted);
  }
</style>
