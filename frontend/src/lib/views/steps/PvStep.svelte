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
  import { emptyRef } from '../../wizard/forms';
  import { issuesAt } from '../../wizard/issues';
  import { emptyPvRow } from '../../wizard/siteForm';
  import { setForecastTemplate } from '../../wizard/tariffForm';

  const save = new SaveState();
  const UNIT = { kwp: 'kWp', deg: '°' } as const;

  let forecasts = $state.raw<TemplateSummary[]>([]);
  let details = $state.raw<Record<string, ForecastTemplate>>({});
  let forecastError = $state(false);

  const pvLive = $derived(
    isUsable(live.flows?.pv_w) ? tPower(live.flows.pv_w.value) : t('pv.live_none'),
  );

  onMount(() => {
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
    for (const id of wizard.tariff.forecastTemplateIds) {
      for (const role of Object.keys(details[id]?.roles ?? {})) {
        wizard.tariff.forecastEntities[role] ??= emptyRef();
      }
    }
  });

  function toggle(id: string, on: boolean): void {
    const roles = Object.keys(details[id]?.roles ?? {});
    setForecastTemplate(wizard.tariff, id, on, roles);
  }

  async function onsave(): Promise<void> {
    // Rows nobody filled in are dropped by the mapping.
    await wizard.saveSite();
    // The tariff section needs import and export prices; until then the choice stays in memory.
    await wizard.saveTariff();
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
    {#if forecastError}
      <p class="notice error" role="alert">{t('api.error.network')}</p>
    {/if}
    {#each forecasts as tpl (tpl.id)}
      {@const on = wizard.tariff.forecastTemplateIds.includes(tpl.id)}
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
          {#each Object.entries(details[tpl.id]?.roles ?? {}) as [role, spec] (role)}
            {#if wizard.tariff.forecastEntities[role]}
              <RolePicker
                label={tDynamic(spec.description, undefined, 'reason.unknown')}
                {role}
                template={tpl.id}
                bind:field={wizard.tariff.forecastEntities[role]}
                optional={!spec.required}
              />
            {/if}
          {/each}
        {/if}
      </div>
    {:else}
      <p class="hint">{t('pv.forecast_none')}</p>
    {/each}
    <p class="hint">{t('pv.forecast_stored')}</p>
  </fieldset>
</WizardShell>

<style>
  .forecast {
    padding: 8px 0;
    border-top: 1px solid var(--divider-color);
  }
</style>
