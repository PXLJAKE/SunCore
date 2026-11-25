<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  // The what-if request form: period, planner settings from the registry (tag `whatif`),
  // battery and tariff alternatives. It only edits a `WhatIfForm`; the view builds and sends
  // the request, the server checks everything again.
  import type { BatteryStrategy, Param } from '../api/types';
  import { hasMessage, t, tDynamic, tNumber, type MessageKey } from '../i18n/index.svelte';
  import {
    MAX_DAYS,
    STRATEGIES,
    issuesOf,
    windowDays,
    type FormIssue,
    type WhatIfForm,
  } from '../whatif/model';
  import FieldIssues from './FieldIssues.svelte';
  import NumberField from './NumberField.svelte';

  interface Props {
    form: WhatIfForm;
    /** The registry parameters tagged `whatif`; `null` while loading, `false` if it failed. */
    params: readonly Param[] | null | false;
    issues: readonly FormIssue[];
    /** The server clock's today (the last day must be earlier). */
    today: string;
    busy: boolean;
    onsubmit: () => void;
  }
  let { form = $bindable(), params, issues, today, busy, onsubmit }: Props = $props();

  const uid = $props.id();

  const STRATEGY: Record<BatteryStrategy, MessageKey> = {
    self_consumption: 'bat.strategy.self_consumption',
    prefer_full: 'bat.strategy.prefer_full',
    target_by_time: 'bat.strategy.target_by_time',
  };

  const days = $derived(
    form.start && form.end && form.end >= form.start ? windowDays(form.start, form.end) : 0,
  );
  const lastDay = $derived(
    // The day before the server's today.
    new Date(Date.parse(`${today}T00:00:00Z`) - 86_400_000).toISOString().slice(0, 10),
  );

  const paramName = (p: Param): string =>
    hasMessage(p.name_key) ? tDynamic(p.name_key, undefined) : p.key;
  const paramHelp = (p: Param): string =>
    hasMessage(p.help_key) ? tDynamic(p.help_key, undefined) : '';
  const paramRaw = (p: Param): string => String(form.params[p.key] ?? p.value ?? '');
  const setParam = (p: Param, raw: string): void => {
    form.params = { ...form.params, [p.key]: raw };
  };

  const range = (p: Param): string =>
    p.minimum !== null || p.maximum !== null
      ? t('expert.range', {
          min: p.minimum === null ? '–' : tNumber(p.minimum, { maximumFractionDigits: 6 }),
          max: p.maximum === null ? '–' : tNumber(p.maximum, { maximumFractionDigits: 6 }),
        }) + (p.unit ? ` ${p.unit}` : '')
      : '';
</script>

<form
  class="stack whatif-form"
  data-testid="whatif-form"
  novalidate
  onsubmit={(e) => {
    e.preventDefault();
    onsubmit();
  }}
>
  <fieldset class="card stack">
    <legend>{t('history.table.period')}</legend>
    <p class="hint">{t('whatif.period.hint', { max: MAX_DAYS })}</p>
    <div class="pair">
      <div class="field">
        <label for="{uid}-start">{t('whatif.period.start')}</label>
        <input
          id="{uid}-start"
          class="input"
          type="date"
          max={lastDay}
          bind:value={form.start}
          aria-describedby="{uid}-window-err"
          data-testid="whatif-start"
        />
      </div>
      <div class="field">
        <label for="{uid}-end">{t('whatif.period.end')}</label>
        <input
          id="{uid}-end"
          class="input"
          type="date"
          max={lastDay}
          bind:value={form.end}
          aria-describedby="{uid}-window-err"
          data-testid="whatif-end"
        />
      </div>
    </div>
    <p class="hint" data-testid="whatif-days">{t('whatif.period.days', { count: days })}</p>
    <FieldIssues issues={issuesOf(issues, 'window')} id="{uid}-window-err" />
  </fieldset>

  <fieldset class="card stack" data-testid="whatif-params">
    <legend>{t('whatif.params.title')}</legend>
    <p class="hint">{t('whatif.params.hint')}</p>
    {#if params === null}
      <p class="hint" role="status">{t('whatif.loading')}</p>
    {:else if params === false}
      <p class="notice error" role="alert">{t('whatif.params.error')}</p>
    {:else if params.length === 0}
      <p class="hint">{t('whatif.params.none')}</p>
    {:else}
      {#each params as p (p.key)}
        {@const own = issuesOf(issues, `param:${p.key}`)}
        <div class="field param" data-testid="whatif-param" data-key={p.key}>
          <label for="{uid}-p-{p.key}">{paramName(p)}</label>
          <div class="with-unit">
            <input
              id="{uid}-p-{p.key}"
              class="input"
              type="number"
              inputmode="decimal"
              min={p.minimum ?? undefined}
              max={p.maximum ?? undefined}
              step={p.type === 'int' ? 1 : 'any'}
              value={paramRaw(p)}
              aria-invalid={own.length > 0 ? 'true' : undefined}
              aria-describedby="{uid}-p-{p.key}-help {uid}-p-{p.key}-err"
              oninput={(e) => setParam(p, e.currentTarget.value)}
            />
            {#if p.unit}<span class="unit" aria-hidden="true">{p.unit}</span>{/if}
          </div>
          <p class="hint" id="{uid}-p-{p.key}-help">
            {#if paramHelp(p)}{paramHelp(p)}<br />{/if}
            {#if range(p)}{range(p)}{/if}
          </p>
          <FieldIssues issues={own} id="{uid}-p-{p.key}-err" />
        </div>
      {/each}
    {/if}
  </fieldset>

  <fieldset class="card stack" data-testid="whatif-battery">
    <legend>{t('battery.title')}</legend>
    <p class="hint">{t('whatif.battery.hint')}</p>
    <div class="field">
      <label for="{uid}-strategy">{t('bat.strategy')}</label>
      <select
        id="{uid}-strategy"
        class="input"
        bind:value={form.strategy}
        data-testid="whatif-strategy"
      >
        <option value="">{t('whatif.as_configured')}</option>
        {#each STRATEGIES as s (s)}
          <option value={s}>{t(STRATEGY[s])}</option>
        {/each}
      </select>
    </div>
    <div class="grid">
      <NumberField
        label={t('energy.path.battery_capacity')}
        bind:value={form.capacityKwh}
        unit="kWh"
        issues={issuesOf(issues, 'capacityKwh')}
      />
      <NumberField
        label={t('whatif.battery.max_charge')}
        bind:value={form.maxChargeKw}
        unit="kW"
        issues={issuesOf(issues, 'maxChargeKw')}
      />
      <NumberField
        label={t('whatif.battery.max_discharge')}
        bind:value={form.maxDischargeKw}
        unit="kW"
        issues={issuesOf(issues, 'maxDischargeKw')}
      />
      <NumberField
        label={t('bat.reserve')}
        bind:value={form.reservePct}
        unit="%"
        issues={issuesOf(issues, 'reservePct')}
      />
      <NumberField
        label={t('whatif.battery.autarky')}
        bind:value={form.autarkyWeight}
        issues={issuesOf(issues, 'autarkyWeight')}
      />
    </div>
    <div class="field">
      <label for="{uid}-grid">{t('whatif.battery.grid_charge')}</label>
      <select
        id="{uid}-grid"
        class="input"
        bind:value={form.gridCharge}
        data-testid="whatif-grid-charge"
      >
        <option value="keep">{t('whatif.as_configured')}</option>
        <option value="off">{t('whatif.battery.grid.off')}</option>
        <option value="on">{t('whatif.battery.grid.on')}</option>
      </select>
    </div>
    {#if form.gridCharge === 'on'}
      <NumberField
        label={t('whatif.battery.grid_price')}
        bind:value={form.gridChargeMaxPrice}
        unit={t('whatif.per_kwh')}
        issues={issuesOf(issues, 'gridChargeMaxPrice')}
      />
    {/if}
  </fieldset>

  <fieldset class="card stack" data-testid="whatif-tariff">
    <legend>{t('settings.section.tariff')}</legend>
    <p class="hint">{t('whatif.tariff.hint')}</p>
    <div class="radios" role="radiogroup" aria-label={t('settings.section.tariff')}>
      <label class="radio">
        <input type="radio" name="{uid}-tariff" value="stored" bind:group={form.tariff} />
        <span>{t('whatif.tariff.stored')}</span>
      </label>
      <label class="radio">
        <input
          type="radio"
          name="{uid}-tariff"
          value="fixed"
          bind:group={form.tariff}
          data-testid="whatif-tariff-fixed"
        />
        <span>{t('whatif.tariff.fixed')}</span>
      </label>
    </div>
    {#if form.tariff === 'fixed'}
      <div class="grid">
        <NumberField
          label={t('whatif.tariff.import')}
          bind:value={form.importPrice}
          unit={t('whatif.per_kwh')}
          issues={issuesOf(issues, 'importPrice')}
        />
        <NumberField
          label={t('whatif.tariff.export')}
          bind:value={form.exportPrice}
          unit={t('whatif.per_kwh')}
          issues={issuesOf(issues, 'exportPrice')}
        />
      </div>
    {/if}
  </fieldset>

  <fieldset class="card stack">
    <legend>{t('whatif.options.title')}</legend>
    <label class="check">
      <input type="checkbox" bind:checked={form.perfect} data-testid="whatif-perfect" />
      <span>{t('whatif.options.perfect')}</span>
    </label>
    <p class="hint">{t('whatif.options.perfect_hint')}</p>
  </fieldset>

  <div class="actions">
    <button type="submit" class="btn" disabled={busy} data-testid="whatif-start-run">
      {t('whatif.start')}
    </button>
  </div>
</form>

<style>
  .whatif-form {
    min-width: 0;
  }
  .pair,
  .grid {
    display: grid;
    gap: 12px 20px;
    grid-template-columns: repeat(auto-fit, minmax(13rem, 1fr));
  }
  .with-unit {
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .unit {
    color: var(--secondary-text-color);
    min-width: 2.5ch;
  }
  .param {
    padding-top: 8px;
    border-top: 1px solid var(--divider-color);
  }
  .actions {
    display: flex;
    gap: 12px;
  }
</style>
