<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import { onMount } from 'svelte';
  import { api } from '../../api/client';
  import { FormError } from '../../api/errors';
  import type { RegionTemplate, TariffTemplate, TemplateSummary } from '../../api/types';
  import FieldIssues from '../../components/FieldIssues.svelte';
  import NumberField from '../../components/NumberField.svelte';
  import RolePicker from '../../components/RolePicker.svelte';
  import WindowFields from '../../components/WindowFields.svelte';
  import WizardShell from '../../components/WizardShell.svelte';
  import { t, tDynamic } from '../../i18n/index.svelte';
  import { SaveState, wizard } from '../../stores/wizard.svelte';
  import { issuesAt } from '../../wizard/issues';
  import {
    addTier,
    formulaApplies,
    isTariffComplete,
    removeTier,
    tiersFromRegion,
    type DynamicSource,
    type ExportKind,
    type ImportKind,
    type PriceIncludes,
  } from '../../wizard/tariffForm';
  import { emptyRef } from '../../wizard/forms';
  import { emptyWindow, isWindowValid } from '../../wizard/scheduleForm';

  const save = new SaveState();
  const uid = $props.id();
  const UNIT = { kwp: 'kWp' } as const;

  let tariffs = $state.raw<TemplateSummary[]>([]);
  let detail = $state.raw<TariffTemplate | null>(null);
  let region = $state.raw<RegionTemplate | undefined>();

  const f = $derived(wizard.tariff);
  const currency = $derived(wizard.region.currency || '');
  const importTemplates = $derived(tariffs.filter((x) => x.kind === 'tariff'));
  const regionTiers = $derived(tiersFromRegion(region));
  const applies = $derived(formulaApplies(f, detail?.price_includes ?? null));
  const extraRoles = $derived(
    Object.entries(detail?.roles ?? {}).filter(([role]) => role !== 'price'),
  );

  // A spot-only source needs its formula: switch it on the first time one applies.
  let formulaOffered = false;
  $effect(() => {
    if (applies && !formulaOffered && !wizard.tariff.formulaEnabled) {
      formulaOffered = true;
      wizard.tariff.formulaEnabled = true;
    }
  });

  // Every extra role of the chosen source gets a field; the stored ones stay.
  $effect(() => {
    for (const [role] of extraRoles) wizard.tariff.roleEntities[role] ??= emptyRef();
  });

  const at = (...p: (string | number)[]) => issuesAt(save.issues, ...p);

  onMount(() => {
    void (async () => {
      try {
        const res = await api.templates('tariff');
        tariffs = res.templates;
        const r = await api.template(wizard.region.regionId).catch(() => null);
        if (r?.template.kind === 'region') region = r.template;
      } catch {
        tariffs = [];
      }
    })();
  });

  $effect(() => {
    const id = f.importTemplateId;
    if (f.importKind !== 'dynamic' || f.dynamicSource !== 'template' || id === '') {
      detail = null;
      return;
    }
    let stale = false;
    api.template(id).then(
      (res) => {
        if (!stale && res.template.kind === 'tariff') detail = res.template;
      },
      () => {
        if (!stale) detail = null;
      },
    );
    return () => {
      stale = true;
    };
  });

  async function onsave(): Promise<void> {
    if (!isTariffComplete(wizard.tariff)) throw new FormError(['tariff.error.incomplete']);
    // An end price never gets the formula on top (fees would count twice).
    if (wizard.tariff.importKind === 'dynamic' && !applies) wizard.tariff.formulaEnabled = false;
    await wizard.saveTariff();
  }
</script>

<WizardShell step="tariff" intro={t('tariff.intro')} {save} {onsave} skippable>
  <fieldset class="card">
    <legend>{t('tariff.import')}</legend>
    <div class="radios" role="radiogroup" aria-label={t('tariff.import')}>
      {#each ['fixed', 'dynamic', 'tou'] as const as kind (kind)}
        <label class="radio">
          <input
            type="radio"
            name="{uid}-import"
            checked={f.importKind === kind}
            onchange={() => (wizard.tariff.importKind = kind as ImportKind)}
          />
          {t(
            kind === 'fixed'
              ? 'tariff.import.fixed'
              : kind === 'dynamic'
                ? 'tariff.import.dynamic'
                : 'tariff.import.tou',
          )}
        </label>
      {/each}
    </div>
    {#if f.importKind === 'tou'}
      <div class="stack" data-testid="tou-editor">
        <p class="hint">{t('tariff.tou_hint')}</p>
        <NumberField
          label={t('tariff.tou_base', { currency })}
          bind:value={wizard.tariff.tou.basePrice}
          min={0}
          step={0.0001}
          issues={at('import', 'tou', 'base_price')}
        />
        {#if f.tou.periods.length === 0}
          <p class="hint" role="status">{t('tariff.tou_none')}</p>
        {/if}
        <ul class="tiers" data-testid="tou-periods">
          {#each f.tou.periods as period, i (i)}
            <li class="tier" data-testid="tou-period">
              <span class="tier-no">{t('tariff.tou_period', { n: i + 1 })}</span>
              <WindowFields
                bind:window={period.window}
                bind:months={period.months}
                label={t('tariff.tou_period', { n: i + 1 })}
              />
              <NumberField
                label={t('tariff.tou_price', { currency })}
                bind:value={period.price}
                min={0}
                step={0.0001}
                issues={at('import', 'tou', 'periods', i, 'price')}
              />
              {#if !isWindowValid(period.window)}
                <p class="hint" role="status">{t('win.no_days')}</p>
              {/if}
              <button
                type="button"
                class="btn secondary small"
                onclick={() => wizard.tariff.tou.periods.splice(i, 1)}
                >{t('win.remove', { n: i + 1 })}</button
              >
            </li>
          {/each}
        </ul>
        <p class="hint">{t('win.crosses_midnight')}</p>
        <div>
          <button
            type="button"
            class="btn secondary"
            data-testid="tou-add"
            onclick={() =>
              wizard.tariff.tou.periods.push({ window: emptyWindow(), price: null, months: [] })}
            >{t('tariff.tou_add')}</button
          >
        </div>
        <FieldIssues issues={at('import', 'tou')} id="{uid}-tou-err" />
      </div>
    {/if}
    {#if f.importKind === 'fixed'}
      <NumberField
        label={t('tariff.price_per_kwh', { currency })}
        bind:value={wizard.tariff.importFixed}
        min={0}
        step={0.0001}
        issues={at('import', 'fixed_price')}
      />
    {:else if f.importKind === 'dynamic'}
      <div class="radios" role="radiogroup" aria-label={t('tariff.dynamic_source')}>
        {#each ['template', 'entity'] as const as src (src)}
          <label class="radio">
            <input
              type="radio"
              name="{uid}-source"
              checked={f.dynamicSource === src}
              onchange={() => (wizard.tariff.dynamicSource = src as DynamicSource)}
            />
            {t(src === 'template' ? 'tariff.source.template' : 'tariff.source.entity')}
          </label>
        {/each}
      </div>
      {#if f.dynamicSource === 'template'}
        <div class="field">
          <label for="{uid}-tpl">{t('tariff.template')}</label>
          <select id="{uid}-tpl" class="input" bind:value={wizard.tariff.importTemplateId}>
            <option value="">{t('tariff.template_choose')}</option>
            {#each importTemplates as tpl (tpl.id)}
              <option value={tpl.id}>{tDynamic(tpl.name, undefined, 'reason.unknown')}</option>
            {/each}
          </select>
          {#if importTemplates.find((x) => x.id === f.importTemplateId)?.tested_with.length === 0 && f.importTemplateId}
            <p class="hint"><span class="chip-tag warn">{t('template.untested')}</span></p>
          {/if}
          <FieldIssues issues={at('import', 'template_id')} id="{uid}-tpl-err" />
        </div>
        {#if detail}
          {#each Object.entries(detail.roles ?? {}) as [role, spec] (role)}
            {#if role === 'price'}
              <RolePicker
                label={tDynamic(spec.description, undefined, 'reason.unknown')}
                {role}
                template={detail.id}
                bind:field={wizard.tariff.priceEntity}
                issues={at('import', 'price_entity')}
              />
            {/if}
          {/each}
        {/if}
      {:else}
        <RolePicker
          label={t('tariff.price_entity')}
          role="price"
          bind:field={wizard.tariff.priceEntity}
          hint={t('tariff.price_entity_hint', { currency })}
          issues={at('import', 'price_entity')}
        />
      {/if}
      {#if extraRoles.length > 0}
        <h4>{t('tariff.role_extra')}</h4>
        {#each extraRoles as [role, spec] (role)}
          {#if wizard.tariff.roleEntities[role]}
            <RolePicker
              label={tDynamic(spec.description, undefined, 'reason.unknown')}
              {role}
              template={detail?.id}
              bind:field={wizard.tariff.roleEntities[role]!}
              optional={!spec.required}
            />
          {/if}
        {/each}
      {/if}
      <div class="field" data-testid="price-includes">
        <label for="{uid}-includes">{t('tariff.includes')}</label>
        <select
          id="{uid}-includes"
          class="input"
          value={f.priceIncludes ?? ''}
          onchange={(e) =>
            (wizard.tariff.priceIncludes = (e.currentTarget.value || null) as PriceIncludes | null)}
        >
          <option value="">{t('tariff.includes.template')}</option>
          <option value="spot_only">{t('tariff.includes.spot_only')}</option>
          <option value="all_in">{t('tariff.includes.all_in')}</option>
        </select>
        <p class="hint">{t('tariff.includes_hint')}</p>
      </div>
      {#if applies}
        <div class="stack" data-testid="formula-editor">
          <h4>{t('tariff.formula')}</h4>
          <label class="check">
            <input type="checkbox" bind:checked={wizard.tariff.formulaEnabled} />
            {t('tariff.formula_use')}
          </label>
          {#if f.formulaEnabled}
            <p class="hint">{t('tariff.formula_hint')}</p>
            <div class="cols">
              <NumberField
                label={t('tariff.formula_multiplier')}
                bind:value={wizard.tariff.formula.spotMultiplier}
                min={0}
                max={10}
                step={0.01}
                issues={at('import', 'formula', 'spot_multiplier')}
              />
              <NumberField
                label={t('tariff.formula_grid_fee', { currency })}
                bind:value={wizard.tariff.formula.gridFee}
                min={0}
                step={0.0001}
                issues={at('import', 'formula', 'grid_fee')}
              />
              <NumberField
                label={t('tariff.formula_levies', { currency })}
                bind:value={wizard.tariff.formula.levies}
                min={0}
                step={0.0001}
                issues={at('import', 'formula', 'levies')}
              />
              <NumberField
                label={t('tariff.formula_margin', { currency })}
                bind:value={wizard.tariff.formula.margin}
                min={0}
                step={0.0001}
                issues={at('import', 'formula', 'margin')}
              />
              <NumberField
                label={t('tariff.formula_vat')}
                bind:value={wizard.tariff.formula.vatPct}
                unit="%"
                min={0}
                max={100}
                step={0.1}
                issues={at('import', 'formula', 'vat_pct')}
              />
            </div>
            <details data-testid="fee-windows">
              <summary>{t('tariff.fee_windows')}</summary>
              <p class="hint">{t('tariff.fee_windows_hint')}</p>
              <ul class="tiers">
                {#each f.formula.feeWindows as row, i (i)}
                  <li class="tier">
                    <span class="tier-no">{t('tariff.fee_n', { n: i + 1 })}</span>
                    <WindowFields
                      bind:window={row.window}
                      bind:months={row.months}
                      label={t('tariff.fee_n', { n: i + 1 })}
                    />
                    <NumberField
                      label={t('tariff.fee_value', { currency })}
                      bind:value={row.fee}
                      min={0}
                      step={0.0001}
                    />
                    <button
                      type="button"
                      class="btn secondary small"
                      onclick={() => wizard.tariff.formula.feeWindows.splice(i, 1)}
                      >{t('win.remove', { n: i + 1 })}</button
                    >
                  </li>
                {/each}
              </ul>
              <div>
                <button
                  type="button"
                  class="btn secondary"
                  onclick={() =>
                    wizard.tariff.formula.feeWindows.push({
                      window: emptyWindow(),
                      fee: null,
                      months: [],
                    })}>{t('tariff.fee_add')}</button
                >
              </div>
            </details>
          {/if}
        </div>
      {/if}
      <FieldIssues issues={at('import')} id="{uid}-imp-err" />
    {/if}
  </fieldset>

  <fieldset class="card">
    <legend>{t('tariff.export')}</legend>
    <div class="radios" role="radiogroup" aria-label={t('tariff.export')}>
      {#each ['fixed', 'tiered', 'none'] as const as kind (kind)}
        <label class="radio">
          <input
            type="radio"
            name="{uid}-export"
            checked={f.exportKind === kind}
            onchange={() => (wizard.tariff.exportKind = kind as ExportKind)}
          />
          {t(
            kind === 'fixed'
              ? 'tariff.export.fixed'
              : kind === 'tiered'
                ? 'tariff.export.tiered'
                : 'tariff.export.none',
          )}
        </label>
      {/each}
    </div>
    {#if f.exportKind === 'fixed'}
      <NumberField
        label={t('tariff.price_per_kwh', { currency })}
        bind:value={wizard.tariff.exportFixed}
        min={0}
        step={0.0001}
        issues={at('export', 'fixed_price')}
      />
    {:else if f.exportKind === 'tiered'}
      <p class="hint">{t('tariff.tiers_hint')}</p>
      {#if regionTiers.length > 0}
        <div>
          <button
            type="button"
            class="btn secondary small"
            onclick={() => (wizard.tariff.tiers = regionTiers.map((x) => ({ ...x })))}
            >{t('tariff.tiers_from_region')}</button
          >
        </div>
      {/if}
      <ul class="tiers" data-testid="tiers">
        {#each f.tiers as tier, i (i)}
          <li class="tier">
            <span class="tier-no">{t('tariff.tier', { n: i + 1 })}</span>
            {#if i < f.tiers.length - 1}
              <NumberField
                label={t('tariff.tier_up_to', { n: i + 1 })}
                bind:value={tier.upToKwp}
                min={0}
                unit={UNIT.kwp}
                issues={at('export', 'tiers', i, 'up_to_kwp')}
              />
            {:else}
              <p class="hint">{t('tariff.tier_unbounded')}</p>
            {/if}
            <NumberField
              label={t('tariff.tier_price', { n: i + 1, currency })}
              bind:value={tier.price}
              min={0}
              step={0.0001}
              issues={at('export', 'tiers', i, 'price')}
            />
            {#if f.tiers.length > 1}
              <button
                type="button"
                class="btn secondary small"
                onclick={() => removeTier(wizard.tariff, i)}
                >{t('tariff.tier_remove', { n: i + 1 })}</button
              >
            {/if}
          </li>
        {/each}
      </ul>
      <div>
        <button type="button" class="btn secondary" onclick={() => addTier(wizard.tariff)}
          >{t('tariff.tier_add')}</button
        >
      </div>
    {:else}
      <p class="hint">{t('tariff.export_none_hint')}</p>
    {/if}
    <FieldIssues issues={at('export')} id="{uid}-exp-err" />
  </fieldset>
</WizardShell>

<style>
  .tiers {
    display: grid;
    gap: 12px;
    margin: 0;
    padding: 0;
    list-style: none;
  }
  .tier {
    display: grid;
    gap: 8px;
    padding: 12px;
    border: 1px solid var(--divider-color);
    border-radius: 8px;
  }
  .tier-no {
    font-weight: 700;
  }
</style>
