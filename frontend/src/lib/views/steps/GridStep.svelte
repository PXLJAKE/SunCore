<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import { FormError } from '../../api/errors';
  import NumberField from '../../components/NumberField.svelte';
  import RolePicker from '../../components/RolePicker.svelte';
  import SignCheck from '../../components/SignCheck.svelte';
  import WizardShell from '../../components/WizardShell.svelte';
  import { live } from '../../stores/live.svelte';
  import { t, tPower, type MessageKey } from '../../i18n/index.svelte';
  import { isUsable } from '../../flow';
  import { SaveState, wizard } from '../../stores/wizard.svelte';
  import { issuesAt } from '../../wizard/issues';
  import {
    validateSiteForm,
    type ExportLimitMode,
    type GridMode,
    type SiteFormError,
  } from '../../wizard/siteForm';

  const save = new SaveState();
  const uid = $props.id();
  const UNIT = { w: 'W', pct: '%' } as const;
  const ERRORS: Record<SiteFormError, MessageKey> = {
    grid_missing: 'grid.error.missing',
    grid_pair_incomplete: 'grid.error.pair_incomplete',
    battery_incomplete: 'battery.error.incomplete',
  };

  const g = $derived(wizard.site.grid);
  const gridLive = $derived(
    isUsable(live.flows?.grid_w) ? tPower(live.flows.grid_w.value) : t('grid.live_none'),
  );
  const at = (...p: (string | number)[]) => issuesAt(save.issues, 'grid', ...p);

  async function onsave(): Promise<void> {
    const errors = validateSiteForm(wizard.site).filter((e) => e !== 'battery_incomplete');
    if (errors.length > 0) throw new FormError(errors.map((e) => ERRORS[e]));
    await wizard.saveSite();
  }
</script>

<WizardShell step="grid" intro={t('grid.intro')} {save} {onsave}>
  <fieldset class="card">
    <legend>{t('grid.sensors')}</legend>
    <p class="hint">{t('grid.live', { value: gridLive })}</p>
    <div class="radios" role="radiogroup" aria-label={t('grid.mode')}>
      {#each ['single', 'pair'] as const as mode (mode)}
        <label class="radio">
          <input
            type="radio"
            name="{uid}-mode"
            value={mode}
            checked={g.mode === mode}
            onchange={() => (wizard.site.grid.mode = mode as GridMode)}
          />
          {t(mode === 'single' ? 'grid.mode.single' : 'grid.mode.pair')}
        </label>
      {/each}
    </div>
    {#if g.mode === 'single'}
      <RolePicker
        label={t('grid.power')}
        role="grid_power"
        bind:field={wizard.site.grid.power}
        signed
        hint={t('grid.power_hint')}
        issues={at('power')}
      />
      <SignCheck kind="grid" path="grid.power" field={wizard.site.grid.power} />
    {:else}
      <RolePicker
        label={t('grid.import_power')}
        role="grid_import_power"
        bind:field={wizard.site.grid.importPower}
        issues={at('import_power')}
      />
      <RolePicker
        label={t('grid.export_power')}
        role="grid_export_power"
        bind:field={wizard.site.grid.exportPower}
        issues={at('export_power')}
      />
      {#if at().length > 0 && at('power').length === 0 && at('import_power').length === 0}
        <p class="hint">{t('grid.pair_hint')}</p>
      {/if}
    {/if}
  </fieldset>

  <fieldset class="card">
    <legend>{t('grid.limits')}</legend>
    <div class="cols">
      <NumberField
        label={t('grid.import_limit')}
        bind:value={wizard.site.grid.importLimitW}
        unit={UNIT.w}
        min={0}
        hint={t('grid.import_limit_hint')}
        issues={at('import_limit_w')}
      />
    </div>
    <div class="field">
      <span class="label" id="{uid}-exp">{t('grid.export_limit')}</span>
      <div class="radios" role="radiogroup" aria-labelledby="{uid}-exp">
        {#each ['none', 'w', 'pct'] as const as mode (mode)}
          <label class="radio">
            <input
              type="radio"
              name="{uid}-export"
              value={mode}
              checked={g.exportLimitMode === mode}
              onchange={() => (wizard.site.grid.exportLimitMode = mode as ExportLimitMode)}
            />
            {t(
              mode === 'none'
                ? 'grid.export.none'
                : mode === 'w'
                  ? 'grid.export.w'
                  : 'grid.export.pct',
            )}
          </label>
        {/each}
      </div>
      {#if g.exportLimitMode === 'w'}
        <NumberField
          label={t('grid.export_limit_w')}
          bind:value={wizard.site.grid.exportLimitW}
          unit={UNIT.w}
          min={0}
          issues={at('export_limit_w')}
        />
      {:else if g.exportLimitMode === 'pct'}
        <NumberField
          label={t('grid.export_limit_pct')}
          bind:value={wizard.site.grid.exportLimitPct}
          unit={UNIT.pct}
          min={0}
          max={100}
          hint={t('grid.export_limit_pct_hint')}
          issues={at('export_limit_pct_kwp')}
        />
      {/if}
    </div>
  </fieldset>

  <fieldset class="card">
    <legend>{t('grid.para14a')}</legend>
    <label class="check">
      <input type="checkbox" bind:checked={wizard.site.grid.para14a} />
      {t('grid.para14a_enable')}
    </label>
    <p class="hint">{t('grid.para14a_hint')}</p>
    {#if g.para14a}
      <RolePicker
        label={t('grid.para14a_signal')}
        role="para14a_signal"
        bind:field={wizard.site.grid.para14aSignal}
        optional
        hint={t('grid.para14a_signal_hint')}
        issues={at('para14a_signal')}
      />
    {/if}
  </fieldset>

  <fieldset class="card">
    <legend>{t('grid.counters')}</legend>
    <p class="hint">{t('grid.counters_hint')}</p>
    <RolePicker
      label={t('grid.energy_import')}
      role="grid_energy_import"
      bind:field={wizard.site.grid.energyImport}
      optional
      issues={at('energy_import')}
    />
    <RolePicker
      label={t('grid.energy_export')}
      role="grid_energy_export"
      bind:field={wizard.site.grid.energyExport}
      optional
      issues={at('energy_export')}
    />
  </fieldset>

  <fieldset class="card">
    <legend>{t('grid.house')}</legend>
    <p class="hint">{t('grid.house_hint')}</p>
    <RolePicker
      label={t('grid.house_power')}
      role="house_power"
      bind:field={wizard.site.house}
      optional
      signed
      issues={issuesAt(save.issues, 'house')}
    />
  </fieldset>
</WizardShell>
