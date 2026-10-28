<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import { onMount } from 'svelte';
  import { api } from '../../api/client';
  import type { ForecastTemplate, TemplateSummary } from '../../api/types';
  import FieldIssues from '../../components/FieldIssues.svelte';
  import NumberField from '../../components/NumberField.svelte';
  import RolePicker from '../../components/RolePicker.svelte';
  import SignCheck from '../../components/SignCheck.svelte';
  import WizardShell from '../../components/WizardShell.svelte';
  import { isUsable } from '../../flow';
  import { t, tDynamic, tPower } from '../../i18n/index.svelte';
  import { live } from '../../stores/live.svelte';
  import { SaveState, wizard } from '../../stores/wizard.svelte';
  import { issuesAt } from '../../wizard/issues';
  import { emptyPvRow } from '../../wizard/siteForm';
  import { ensureRoleFields, setForecastTemplate } from '../../wizard/forecastForm';

  const save = new SaveState();
  const UNIT = { kwp: 'kWp', deg: '°' } as const;

  let forecasts = $state.raw<TemplateSummary[]>([]);
  let details = $state.raw<Record<string, ForecastTemplate>>({});
  let forecastError = $state(false);
  /** Config entries the Energy dashboard links to its solar sources (Forecast.Solar). */
  let entries = $state.raw<string[]>([]);
  const uid = $props.id();

  const pvLive = $derived(
    isUsable(live.flows?.pv_w) ? tPower(live.flows.pv_w.value) : t('pv.live_none'),
  );

  onMount(() => {
    void api
      .energyPrefs()
      .then((p) => (entries = p.solar_forecast_entries ?? []))
      .catch(() => (entries = []));
    if (wizard.site.pv.length === 0) wizard.site.pv.push(emptyPvRow());
    void (async () => {
      try {
        const res = await api.templates('forecast');
        forecasts = res.templates;
        const loaded = await Promise.all(
          res.templates.map((x) => api.template(x.id).catch(() => null)),
        );
        const map: Record<string, ForecastTemplate> = {};
        for (const d of loaded)
          if (d?.template.kind === 'forecast') map[d.template.id] = d.template;
        details = map;
      } catch {
        forecastError = true;
      }
    })();
  });

  // Every role of a chosen forecast template gets a field, also when it was stored without one.
  $effect(() => {
    for (const id of wizard.forecast.templateIds) {
      ensureRoleFields(wizard.forecast, id, Object.keys(details[id]?.roles ?? {}));
    }
  });

  function toggle(id: string, on: boolean): void {
    const roles = Object.keys(details[id]?.roles ?? {});
    setForecastTemplate(wizard.forecast, id, on, roles);
  }

  async function onsave(): Promise<void> {
    // Rows nobody filled in are dropped by the mapping.
    await wizard.saveSite();
    // The forecast sources are their own section: no prices needed to keep the choice.
    await wizard.saveForecast();
  }

  const isEntrySource = (id: string): boolean =>
    details[id]?.series?.source === 'energy_solar_forecast';
  const count = $derived(wizard.forecast.templateIds.length);
  const usesOpenMeteo = $derived(
    wizard.forecast.templateIds.some((id) => id.startsWith('open_meteo')),
  );

  function setWeightMode(id: string, manual: boolean): void {
    const current = wizard.forecast.weights[id];
    wizard.forecast.weights[id] = manual
      ? { weight_mode: 'manual', weight: current?.weight ?? 0.5 }
      : { weight_mode: 'auto', weight: null };
  }

  const at = (i: number, ...p: (string | number)[]) => issuesAt(save.issues, 'pv', i, ...p);
</script>

<WizardShell step="pv" intro={t('pv.intro')} {save} {onsave} skippable>
  <p class="hint">{t('pv.live', { value: pvLive })}</p>
  {#each wizard.site.pv as row, i (i)}
    <fieldset class="card" data-testid="pv-array">
      <legend>{t('pv.array', { n: i + 1 })}</legend>
      <div class="field">
        <label for="pv-name-{i}">{t('pv.name')}</label>
        <input
          id="pv-name-{i}"
          class="input"
          bind:value={row.name}
          placeholder={t('pv.name_placeholder', { n: i + 1 })}
          autocomplete="off"
        />
        <FieldIssues issues={at(i, 'name')} id="pv-name-{i}-err" />
      </div>
      <RolePicker
        label={t('pv.power')}
        role="pv_power"
        bind:field={row.power}
        signed
        issues={at(i, 'power')}
      />
      <SignCheck kind="pv" path="pv.{i}.power" field={row.power} />
      <div class="cols">
        <NumberField
          label={t('pv.kwp')}
          bind:value={row.kwp}
          unit={UNIT.kwp}
          min={0}
          hint={t('pv.kwp_hint')}
          issues={at(i, 'kwp')}
        />
        <NumberField
          label={t('pv.azimuth')}
          bind:value={row.azimuth}
          unit={UNIT.deg}
          min={0}
          max={360}
          hint={t('pv.azimuth_hint')}
          issues={at(i, 'azimuth_deg')}
        />
        <NumberField
          label={t('pv.tilt')}
          bind:value={row.tilt}
          unit={UNIT.deg}
          min={0}
          max={90}
          issues={at(i, 'tilt_deg')}
        />
      </div>
      <div class="field">
        <label for="pv-inv-{i}"
          >{t('pv.inverter')} <span class="hint">{t('field.optional')}</span></label
        >
        <input id="pv-inv-{i}" class="input" bind:value={row.inverter} autocomplete="off" />
        <FieldIssues issues={at(i, 'inverter')} id="pv-inv-{i}-err" />
      </div>
      {#if wizard.site.pv.length > 1}
        <div>
          <button
            type="button"
            class="btn secondary small"
            onclick={() => wizard.site.pv.splice(i, 1)}>{t('pv.remove', { n: i + 1 })}</button
          >
        </div>
      {/if}
    </fieldset>
  {/each}
  <div>
    <button type="button" class="btn secondary" onclick={() => wizard.site.pv.push(emptyPvRow())}
      >{t('pv.add')}</button
    >
  </div>

  <fieldset class="card">
    <legend>{t('pv.forecast')}</legend>
    <p class="hint">{t('pv.forecast_hint')}</p>
    <p class="hint">{t('pvf.sources_hint')}</p>
    <p class="hint" role="status" data-testid="forecast-count">{t('pvf.count', { count })}</p>
    {#if forecastError}
      <p class="notice error" role="alert">{t('api.error.network')}</p>
    {/if}
    {#each forecasts as tpl (tpl.id)}
      {@const on = wizard.forecast.templateIds.includes(tpl.id)}
      <div class="forecast stack">
        <label class="check">
          <input
            type="checkbox"
            checked={on}
            onchange={(e) => toggle(tpl.id, e.currentTarget.checked)}
          />
          {tDynamic(tpl.name, undefined, 'reason.unknown')}
        </label>
        {#if tpl.unverified.length > 0 && tpl.tested_with.length === 0}
          <p class="hint"><span class="chip-tag warn">{t('template.untested')}</span></p>
        {/if}
        {#if on}
          {#if isEntrySource(tpl.id)}
            <div class="field" data-testid="entry-field">
              {#if entries.length > 0}
                <label for="{uid}-{tpl.id}-entry">{t('pvf.entry')}</label>
                <select
                  id="{uid}-{tpl.id}-entry"
                  class="input"
                  value={wizard.forecast.configEntries[tpl.id] ?? ''}
                  onchange={(e) => (wizard.forecast.configEntries[tpl.id] = e.currentTarget.value)}
                >
                  <option value=""
                    >{entries.length === 1 ? t('pvf.entry_auto') : t('pvf.entry_choose')}</option
                  >
                  {#each entries as entry (entry)}<option value={entry}>{entry}</option>{/each}
                </select>
                <p class="hint">{t(entries.length === 1 ? 'pvf.entry_only' : 'pvf.entry_hint')}</p>
              {:else}
                <label for="{uid}-{tpl.id}-entry">{t('pvf.entry_manual')}</label>
                <input
                  id="{uid}-{tpl.id}-entry"
                  class="input"
                  autocomplete="off"
                  value={wizard.forecast.configEntries[tpl.id] ?? ''}
                  oninput={(e) => (wizard.forecast.configEntries[tpl.id] = e.currentTarget.value)}
                />
                <p class="hint">{t('pvf.entry_manual_hint')}</p>
              {/if}
            </div>
          {/if}
          {#if count > 1}
            <div class="field" role="group" aria-label={t('pvf.weight')}>
              <span class="label">{t('pvf.weight')}</span>
              <div class="radios">
                <label class="radio">
                  <input
                    type="radio"
                    name="{uid}-{tpl.id}-weight"
                    checked={wizard.forecast.weights[tpl.id]?.weight_mode !== 'manual'}
                    onchange={() => setWeightMode(tpl.id, false)}
                  />
                  {t('pvf.weight_auto')}
                </label>
                <label class="radio">
                  <input
                    type="radio"
                    name="{uid}-{tpl.id}-weight"
                    checked={wizard.forecast.weights[tpl.id]?.weight_mode === 'manual'}
                    onchange={() => setWeightMode(tpl.id, true)}
                  />
                  {t('pvf.weight_manual')}
                </label>
              </div>
              {#if wizard.forecast.weights[tpl.id]?.weight_mode === 'manual'}
                <NumberField
                  label={t('pvf.weight_share')}
                  bind:value={
                    () => wizard.forecast.weights[tpl.id]?.weight ?? null,
                    (v) => (wizard.forecast.weights[tpl.id] = { weight_mode: 'manual', weight: v })
                  }
                  min={0.01}
                  max={1}
                  step={0.05}
                  hint={t('pvf.weight_hint')}
                />
              {/if}
            </div>
          {/if}
          {#each Object.entries(details[tpl.id]?.roles ?? {}) as [role, spec] (role)}
            {#if wizard.forecast.entities[tpl.id]?.[role]}
              <RolePicker
                label={tDynamic(spec.description, undefined, 'reason.unknown')}
                {role}
                template={tpl.id}
                bind:field={wizard.forecast.entities[tpl.id]![role]!}
                optional={!spec.required}
              />
            {/if}
          {/each}
        {/if}
      </div>
    {:else}
      <p class="hint">{t('pv.forecast_none')}</p>
    {/each}
    {#if usesOpenMeteo}<p class="hint" data-testid="open-meteo-note">
        {t('pvf.open_meteo_note')}
      </p>{/if}
  </fieldset>

  <fieldset class="card">
    <legend>{t('pvf.load')}</legend>
    <p class="hint">{t('pvf.load_hint')}</p>
    <RolePicker
      label={t('pvf.workday')}
      role="workday"
      bind:field={wizard.forecast.workday}
      optional
    />
    <RolePicker
      label={t('pvf.weather')}
      role="weather"
      bind:field={wizard.forecast.weather}
      optional
    />
  </fieldset>
</WizardShell>

<style>
  .forecast {
    padding: 8px 0;
    border-top: 1px solid var(--divider-color);
  }
</style>
