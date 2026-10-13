<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import { onMount } from 'svelte';
  import { api } from '../../api/client';
  import { describeError, FormError } from '../../api/errors';
  import type { RegionTemplate, TemplateSummary } from '../../api/types';
  import FieldIssues from '../../components/FieldIssues.svelte';
  import WizardShell from '../../components/WizardShell.svelte';
  import { i18n, t, tDynamic } from '../../i18n/index.svelte';
  import { SaveState, wizard } from '../../stores/wizard.svelte';
  import { issuesAt } from '../../wizard/issues';
  import { applyRegionTemplate, validateRegionForm, type Language } from '../../wizard/regionForm';

  const save = new SaveState();
  const uid = $props.id();
  const CURRENCIES = ['EUR', 'CHF', 'GBP', 'USD', 'SEK', 'NOK', 'DKK', 'PLN', 'CZK'];
  const LANGUAGES: {
    value: Language;
    label: 'region.lang.auto' | 'region.lang.en' | 'region.lang.de';
  }[] = [
    { value: 'auto', label: 'region.lang.auto' },
    { value: 'en', label: 'region.lang.en' },
    { value: 'de', label: 'region.lang.de' },
  ];

  let templates = $state.raw<TemplateSummary[]>([]);
  let loadError = $state<string | null>(null);
  let details = $state.raw<Record<string, RegionTemplate>>({});
  let zones = $state.raw<string[]>([]);
  let clientErrors = $state.raw<('currency' | 'timezone' | 'region')[]>([]);
  const REGION_ERRORS = {
    region: 'region.error.region',
    currency: 'region.error.currency',
    timezone: 'region.error.timezone',
  } as const;

  const form = $derived(wizard.region);
  const selected = $derived(details[form.regionId]);

  async function choose(id: string): Promise<void> {
    wizard.region.regionId = id;
    try {
      let detail = details[id];
      if (!detail) {
        const res = await api.template(id);
        if (res.template.kind !== 'region') return;
        detail = res.template;
        details = { ...details, [id]: detail };
      }
      applyRegionTemplate(wizard.region, detail);
    } catch {
      // The region stays chosen; currency and time zone can be typed.
    }
  }

  onMount(() => {
    try {
      zones =
        (Intl as unknown as { supportedValuesOf?: (k: string) => string[] }).supportedValuesOf?.(
          'timeZone',
        ) ?? [];
    } catch {
      zones = [];
    }
    void (async () => {
      try {
        const res = await api.templates('region');
        templates = res.templates;
        // First start: a German browser most likely wants the German profile.
        if (
          wizard.saved.region === null &&
          i18n.locale === 'de' &&
          res.templates.some((x) => x.id === 'de')
        ) {
          await choose('de');
        } else if (wizard.saved.region !== null) {
          const detail = await api.template(wizard.region.regionId).catch(() => null);
          if (detail?.template.kind === 'region') {
            details = { ...details, [detail.template.id]: detail.template };
          }
        }
      } catch (e) {
        loadError = describeError(e).key;
      }
    })();
  });

  async function onsave(): Promise<void> {
    const errors = validateRegionForm(wizard.region);
    clientErrors = errors;
    if (errors.length > 0) throw new FormError(errors.map((e) => REGION_ERRORS[e]));
    await wizard.saveRegion();
  }
</script>

<WizardShell step="region" intro={t('region.intro')} {save} {onsave}>
  <div class="stack">
    <div class="field">
      <label for="{uid}-region">{t('region.profile')}</label>
      <select
        id="{uid}-region"
        class="input"
        value={form.regionId}
        onchange={(e) => void choose(e.currentTarget.value)}
      >
        {#each templates as tpl (tpl.id)}
          <option value={tpl.id}>{tDynamic(tpl.name, undefined, 'region.profile_unknown')}</option>
        {:else}
          <option value={form.regionId}>{form.regionId}</option>
        {/each}
      </select>
      <p class="hint">{t('region.profile_hint')}</p>
      {#if loadError}<p class="notice error" role="alert">
          {tDynamic(loadError, undefined, 'api.error.unknown')}
        </p>{/if}
      {#if selected && selected.negative_price_rule !== 'none'}
        <p class="hint">{t(`region.rule.${selected.negative_price_rule}`)}</p>
      {/if}
    </div>

    <div class="cols">
      <div class="field">
        <label for="{uid}-currency">{t('region.currency')}</label>
        <input
          id="{uid}-currency"
          class="input"
          list="{uid}-currencies"
          maxlength="3"
          autocomplete="off"
          bind:value={wizard.region.currency}
          aria-invalid={clientErrors.includes('currency') ||
          issuesAt(save.issues, 'currency').length > 0
            ? 'true'
            : undefined}
        />
        <datalist id="{uid}-currencies">
          {#each CURRENCIES as c (c)}<option value={c}></option>{/each}
        </datalist>
        <p class="hint">{t('region.currency_hint')}</p>
        <FieldIssues issues={issuesAt(save.issues, 'currency')} id="{uid}-currency-err" />
      </div>

      <div class="field">
        <label for="{uid}-tz">{t('region.timezone')}</label>
        <input
          id="{uid}-tz"
          class="input"
          list="{uid}-zones"
          autocomplete="off"
          spellcheck="false"
          bind:value={wizard.region.timezone}
          aria-invalid={clientErrors.includes('timezone') ||
          issuesAt(save.issues, 'timezone').length > 0
            ? 'true'
            : undefined}
        />
        <datalist id="{uid}-zones">
          {#each zones as z (z)}<option value={z}></option>{/each}
        </datalist>
        <p class="hint">{t('region.timezone_hint')}</p>
        <FieldIssues issues={issuesAt(save.issues, 'timezone')} id="{uid}-tz-err" />
      </div>
    </div>

    <fieldset class="field lang">
      <legend class="label">{t('region.language')}</legend>
      <div class="radios">
        {#each LANGUAGES as l (l.value)}
          <label class="radio">
            <input
              type="radio"
              name="{uid}-lang"
              value={l.value}
              checked={form.language === l.value}
              onchange={() => (wizard.region.language = l.value)}
            />
            {t(l.label)}
          </label>
        {/each}
      </div>
      <p class="hint">{t('region.language_hint')}</p>
    </fieldset>
  </div>
</WizardShell>

<style>
  .lang {
    border: 0;
    padding: 0;
    margin: 0;
    min-width: 0;
  }
  .lang legend {
    padding: 0;
    margin-bottom: 4px;
  }
</style>
