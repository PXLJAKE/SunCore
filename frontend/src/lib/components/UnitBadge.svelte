<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import { t } from '../i18n/index.svelte';

  interface Props {
    /** Unit as reported by Home Assistant; empty = unknown. */
    unit: string;
    /** `false` = the unit does not fit the role (e.g. a % sensor for a power role). */
    ok?: boolean | null;
  }
  let { unit, ok = null }: Props = $props();
</script>

<span class="unit" class:warn={ok === false} data-testid="unit-badge">
  {unit.trim() === '' ? t('unit.unknown') : unit}
  {#if ok === false}<span class="sr-only">{t('unit.mismatch')}</span><span aria-hidden="true"
      >!</span
    >{/if}
</span>

<style>
  .unit {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    padding: 0 8px;
    min-height: 22px;
    border-radius: 11px;
    border: 1px solid var(--divider-color);
    background: var(--secondary-background-color);
    color: var(--primary-text-color);
    font-size: 0.8125rem;
    font-weight: 600;
    white-space: nowrap;
  }
  .unit.warn {
    border-color: var(--warning-color);
    color: var(--warning-color);
    background: transparent;
  }
</style>
