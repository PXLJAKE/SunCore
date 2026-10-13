<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import { onMount } from 'svelte';
  import { api } from '../../api/client';
  import { FormError } from '../../api/errors';
  import type { RegionTemplate, TariffTemplate, TemplateSummary } from '../../api/types';
  import FieldIssues from '../../components/FieldIssues.svelte';
  import NumberField from '../../components/NumberField.svelte';
  import RolePicker from '../../components/RolePicker.svelte';
  import WizardShell from '../../components/WizardShell.svelte';
  import { t, tDynamic } from '../../i18n/index.svelte';
  import { SaveState, wizard } from '../../stores/wizard.svelte';
  import { issuesAt } from '../../wizard/issues';
  import {
    addTier,
    isTariffComplete,
    removeTier,
    tiersFromRegion,
    type DynamicSource,
    type ExportKind,
    type ImportKind,
  } from '../../wizard/tariffForm';

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
    await wizard.saveTariff();
  }
</script>

<WizardShell step="tariff" intro={t('tariff.intro')} {save} {onsave} skippable>
  <fieldset class="card">
    <legend>{t('tariff.import')}</legend>
    <div class="radios" role="radiogroup" aria-label={t('tariff.import')}>
      {#each ['fixed', 'dynamic'] as const as kind (kind)}
        <label class="radio">
          <input
            type="radio"
            name="{uid}-import"
            checked={f.importKind === kind}
            onchange={() => (wizard.tariff.importKind = kind as ImportKind)}
          />
          {t(kind === 'fixed' ? 'tariff.import.fixed' : 'tariff.import.dynamic')}
        </label>
      {/each}
    </div>
    {#if f.importKind === 'fixed'}
      <NumberField
        label={t('tariff.price_per_kwh', { currency })}
        bind:value={wizard.tariff.importFixed}
        min={0}
        step={0.0001}
        issues={at('import', 'fixed_price')}
      />
    {:else}
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
