<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import { FormError } from '../../api/errors';
  import FieldIssues from '../../components/FieldIssues.svelte';
  import NumberField from '../../components/NumberField.svelte';
  import RolePicker from '../../components/RolePicker.svelte';
  import SignCheck from '../../components/SignCheck.svelte';
  import WizardShell from '../../components/WizardShell.svelte';
  import Disclosure from '../../ui/Disclosure.svelte';
  import { live } from '../../stores/live.svelte';
  import { t, tPower, type MessageKey } from '../../i18n/index.svelte';
  import { isUsable } from '../../flow';
  import type { EntityCandidate } from '../../api/types';
  import { candidateValueText } from '../../entityFormat';
  import { SaveState, wizard } from '../../stores/wizard.svelte';
  import { issuesAt } from '../../wizard/issues';
  import {
    validateSiteForm,
    type ExportLimitMode,
    type GridMode,
    parseIslandStates,
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
  let backupCandidate = $state.raw<EntityCandidate | null>(null);
  const islandStates = $derived(parseIslandStates(g.backupStates));
  const backupPreview = $derived.by(() => {
    const c = backupCandidate;
    if (!c) return null;
    const state = candidateValueText(c);
    const island = c.available && islandStates.includes(c.state);
    return { state, island };
  });
  const at = (...p: (string | number)[]) => issuesAt(save.issues, 'grid', ...p);

  // Optional parts start closed unless something in them is set or has a server issue.
  const OPERATOR_PATHS = ['peak_shaving', 'para14a_signal', 'para14a_limit_w', 'backup'] as const;
  let operatorOpen = $state(false);
  let sensorsOpen = $state(false);
  $effect(() => {
    if (
      g.peakEnabled ||
      g.zeroExport ||
      g.exportDynamic ||
      g.para14a ||
      g.backupEnabled ||
      OPERATOR_PATHS.some((path) => at(path).length > 0)
    ) {
      operatorOpen = true;
    }
  });
  $effect(() => {
    if (
      g.energyImport.entity_id ||
      g.energyExport.entity_id ||
      wizard.site.house.entity_id ||
      at('energy_import').length + at('energy_export').length > 0 ||
      issuesAt(save.issues, 'house').length > 0
    ) {
      sensorsOpen = true;
    }
  });

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

  <Disclosure
    summary={t('wizard.grid_more_operator')}
    bind:open={operatorOpen}
    testid="grid-operator"
  >
    <fieldset class="card">
      <legend>{t('grid.peak')}</legend>
      <label class="check">
        <input type="checkbox" bind:checked={wizard.site.grid.peakEnabled} />
        {t('grid.peak_enable')}
      </label>
      <p class="hint">{t('grid.peak_hint')}</p>
      {#if g.peakEnabled}
        <div class="cols">
          <NumberField
            label={t('grid.peak_floor')}
            bind:value={wizard.site.grid.peakFloorW}
            unit={UNIT.w}
            min={0}
            hint={t('grid.peak_floor_hint')}
            issues={at('peak_shaving', 'peak_floor_w')}
          />
          <NumberField
            label={t('grid.peak_price')}
            bind:value={wizard.site.grid.peakPrice}
            unit={t('grid.peak_price_unit')}
            min={0}
            hint={t('grid.peak_price_hint')}
            issues={at('peak_shaving', 'capacity_price_per_kw_month')}
          />
        </div>
      {/if}
    </fieldset>

    <fieldset class="card">
      <legend>{t('grid.export_chain')}</legend>
      <p class="hint">{t('grid.export_chain_hint')}</p>
      <label class="check">
        <input type="checkbox" bind:checked={wizard.site.grid.zeroExport} />
        {t('grid.zero_export')}
      </label>
      <p class="hint">{t('grid.zero_export_hint')}</p>
      <label class="check">
        <input type="checkbox" bind:checked={wizard.site.grid.exportDynamic} />
        {t('grid.export_dynamic')}
      </label>
      <p class="hint">{t('grid.export_dynamic_hint')}</p>
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
        <div class="radios" role="radiogroup" aria-label={t('grid.para14a_kind')}>
          {#each ['binary', 'power_w'] as const as kind (kind)}
            <label class="radio">
              <input
                type="radio"
                name="{uid}-kind"
                value={kind}
                checked={g.para14aSignalKind === kind}
                onchange={() => (wizard.site.grid.para14aSignalKind = kind)}
              />
              {t(kind === 'binary' ? 'grid.para14a_kind.binary' : 'grid.para14a_kind.power_w')}
            </label>
          {/each}
        </div>
        <NumberField
          label={t('grid.para14a_limit')}
          bind:value={wizard.site.grid.para14aLimitW}
          unit={UNIT.w}
          min={0}
          hint={t('grid.para14a_limit_hint')}
          issues={at('para14a_limit_w')}
        />
      {/if}
    </fieldset>

    <fieldset class="card">
      <legend>{t('grid.backup')}</legend>
      <label class="check">
        <input type="checkbox" bind:checked={wizard.site.grid.backupEnabled} />
        {t('grid.backup_enable')}
      </label>
      <p class="hint">{t('grid.backup_hint')}</p>
      {#if g.backupEnabled}
        <RolePicker
          label={t('grid.backup_entity')}
          role="backup_state"
          bind:field={wizard.site.grid.backup}
          hint={t('grid.backup_entity_hint')}
          issues={at('backup', 'entity_id')}
          oncandidate={(c) => (backupCandidate = c)}
        />
        <div class="field">
          <label class="label" for="{uid}-island">{t('grid.backup_states')}</label>
          <input
            id="{uid}-island"
            class="input"
            type="text"
            bind:value={wizard.site.grid.backupStates}
            aria-describedby="{uid}-island-hint {uid}-island-issues"
          />
          <p class="hint" id="{uid}-island-hint">{t('grid.backup_states_hint')}</p>
          <FieldIssues issues={at('backup', 'island_states')} id="{uid}-island-issues" />
        </div>
        {#if backupPreview}
          <p class="hint" role="status" data-testid="backup-preview">
            {t('grid.backup_preview', {
              state: backupPreview.state,
              island: backupPreview.island ? 'yes' : 'no',
            })}
          </p>
        {/if}
      {/if}
    </fieldset>
  </Disclosure>

  <Disclosure
    summary={t('wizard.grid_more_sensors')}
    bind:open={sensorsOpen}
    testid="grid-sensors-more"
  >
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
  </Disclosure>
</WizardShell>
