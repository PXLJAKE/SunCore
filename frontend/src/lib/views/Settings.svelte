<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import { onMount } from 'svelte';
  import { api, ApiError } from '../api/client';
  import type { EntityCandidate, ValidationIssue } from '../api/types';
  import EntityField from '../components/EntityField.svelte';
  import { isUsable } from '../flow';
  import { t, tDynamic, tPercent, tPower } from '../i18n/index.svelte';
  import {
    emptyForm,
    validateForm,
    type FormErrorKey,
    emptyRef,
    formFromSite,
    siteFromForm,
    type GridMode,
    type SiteForm,
  } from '../siteForm';
  import { app } from '../stores/app.svelte';
  import { live } from '../stores/live.svelte';

  const LIST_ID = 'suncore-entity-options';
  const POLL_MS = 10_000;

  let form = $state<SiteForm>(emptyForm());
  let loaded = $state(false);
  let candidates = $state.raw<EntityCandidate[]>([]);
  let entitiesError = $state<string | null>(null);
  let saving = $state(false);
  let saved = $state(false);
  let saveError = $state<string | null>(null);
  let issues = $state.raw<ValidationIssue[]>([]);
  let formErrors = $state.raw<FormErrorKey[]>([]);

  const flows = $derived(live.flows);
  const pvCount = $derived(form.pv.filter((p) => p.entity_id.trim() !== '').length);

  async function loadEntities(): Promise<void> {
    try {
      candidates = await api.entities();
      entitiesError = null;
    } catch (e) {
      entitiesError = e instanceof ApiError ? (e.key ?? 'api.error.network') : 'api.error.network';
    }
  }

  onMount(() => {
    void (async () => {
      try {
        form = formFromSite(await api.getSite());
      } catch (e) {
        saveError = e instanceof ApiError ? (e.key ?? 'api.error.network') : 'api.error.network';
      }
      loaded = true;
    })();
    void loadEntities();
    // Each call is a full get_states in HA: slow polling, paused while the tab is hidden.
    let timer: ReturnType<typeof setInterval> | null = null;
    const startPolling = () => {
      if (timer === null) timer = setInterval(() => void loadEntities(), POLL_MS);
    };
    const stopPolling = () => {
      if (timer !== null) clearInterval(timer);
      timer = null;
    };
    const onVisibility = () => {
      if (document.hidden) {
        stopPolling();
      } else {
        void loadEntities();
        startPolling();
      }
    };
    if (!document.hidden) startPolling();
    document.addEventListener('visibilitychange', onVisibility);
    return () => {
      stopPolling();
      document.removeEventListener('visibilitychange', onVisibility);
    };
  });

  function liveText(id: 'grid_w' | 'pv_w' | 'house_w'): string {
    const f = flows?.[id];
    return isUsable(f) ? tPower(f.value) : t('settings.live.none');
  }
  const batteryLive = $derived.by(() => {
    const p = flows?.battery_w;
    const s = flows?.battery_soc_pct;
    if (!isUsable(p)) return t('settings.live.none');
    return isUsable(s)
      ? t('settings.live.battery', { power: tPower(p.value), soc: tPercent(s.value) })
      : tPower(p.value);
  });

  async function save(ev: SubmitEvent): Promise<void> {
    ev.preventDefault();
    saved = false;
    saveError = null;
    issues = [];
    formErrors = validateForm(form);
    if (formErrors.length > 0) return;
    saving = true;
    saveError = null;
    issues = [];
    try {
      await api.putSite(siteFromForm(form));
      saved = true;
      await app.reloadSite();
    } catch (e) {
      if (e instanceof ApiError) {
        issues = e.issues;
        saveError =
          e.status === 422 && !e.key ? 'settings.error.validation' : (e.key ?? 'api.error.network');
      } else {
        saveError = 'api.error.network';
      }
    } finally {
      saving = false;
    }
  }

  function fieldPath(issue: ValidationIssue): string {
    // loc is ["body", ...]; the leading "body" is noise for the user.
    return issue.loc.filter((p, i) => !(i === 0 && p === 'body')).join(' / ');
  }
</script>

<section class="settings" aria-labelledby="settings-title">
  <h2 id="settings-title">{t('settings.title')}</h2>
  <p class="intro">{t('settings.intro')}</p>

  {#if entitiesError}
    <p class="notice error" role="alert">
      {tDynamic(entitiesError, undefined, 'api.error.unknown')}
    </p>
  {/if}

  <datalist id={LIST_ID}>
    {#each candidates as c (c.entity_id)}
      <option value={c.entity_id}>{c.name}</option>
    {/each}
  </datalist>

  {#if loaded}
    <form onsubmit={save} novalidate>
      <fieldset class="card">
        <legend>{t('settings.grid.title')}</legend>
        <p class="live">{t('settings.live', { value: liveText('grid_w') })}</p>
        <div class="radios" role="radiogroup" aria-label={t('settings.grid.mode')}>
          {#each ['single', 'pair'] as const as mode (mode)}
            <label class="radio">
              <input
                type="radio"
                name="grid-mode"
                value={mode}
                checked={form.gridMode === mode}
                onchange={() => (form.gridMode = mode as GridMode)}
              />
              {t(mode === 'single' ? 'settings.grid.single' : 'settings.grid.pair')}
            </label>
          {/each}
        </div>
        {#if form.gridMode === 'single'}
          <EntityField
            label={t('settings.grid.power')}
            bind:field={form.gridPower}
            {candidates}
            listId={LIST_ID}
          />
        {:else}
          <EntityField
            label={t('settings.grid.import')}
            bind:field={form.gridImport}
            {candidates}
            listId={LIST_ID}
          />
          <EntityField
            label={t('settings.grid.export')}
            bind:field={form.gridExport}
            {candidates}
            listId={LIST_ID}
          />
        {/if}
      </fieldset>

      <fieldset class="card">
        <legend>{t('settings.pv.title')}</legend>
        <p class="live">
          {t('settings.pv.count', { count: pvCount })} · {t('settings.live', {
            value: liveText('pv_w'),
          })}
        </p>
        {#each form.pv as _, i (i)}
          <EntityField
            label={t('settings.pv.entity', { n: i + 1 })}
            bind:field={form.pv[i]!}
            {candidates}
            listId={LIST_ID}
            onremove={form.pv.length > 1 ? () => form.pv.splice(i, 1) : undefined}
          />
        {/each}
        <div>
          <button type="button" class="btn secondary" onclick={() => form.pv.push(emptyRef())}
            >{t('settings.pv.add')}</button
          >
        </div>
      </fieldset>

      <fieldset class="card">
        <legend>{t('settings.battery.title')}</legend>
        <label class="check">
          <input type="checkbox" bind:checked={form.batteryEnabled} />
          {t('settings.battery.enable')}
        </label>
        {#if form.batteryEnabled}
          <p class="live">{t('settings.live', { value: batteryLive })}</p>
          <EntityField
            label={t('settings.battery.power')}
            bind:field={form.batteryPower}
            {candidates}
            listId={LIST_ID}
          />
          <EntityField
            label={t('settings.battery.soc')}
            bind:field={form.batterySoc}
            {candidates}
            kind="soc"
            signed={false}
            listId={LIST_ID}
          />
        {/if}
      </fieldset>

      <fieldset class="card">
        <legend>{t('settings.house.title')}</legend>
        <p class="hint">{t('settings.house.hint')}</p>
        <p class="live">{t('settings.live', { value: liveText('house_w') })}</p>
        <EntityField
          label={t('settings.house.entity')}
          bind:field={form.house}
          {candidates}
          listId={LIST_ID}
        />
      </fieldset>

      {#if formErrors.length > 0}
        <div class="notice error" role="alert" data-testid="form-error">
          <ul>
            {#each formErrors as key (key)}
              <li>{t(key)}</li>
            {/each}
          </ul>
        </div>
      {/if}
      {#if saveError}
        <div class="notice error" role="alert" data-testid="save-error">
          <p>{tDynamic(saveError, undefined, 'api.error.unknown')}</p>
          {#if issues.length > 0}
            <ul>
              {#each issues as issue, i (i)}
                <li><span class="loc">{fieldPath(issue)}</span> {issue.msg}</li>
              {/each}
            </ul>
          {/if}
        </div>
      {/if}
      {#if saved}
        <p class="notice ok" role="status" data-testid="saved">{t('settings.saved')}</p>
      {/if}

      <div class="actions">
        <button class="btn" type="submit" disabled={saving}>
          {saving ? t('settings.saving') : t('settings.save')}
        </button>
      </div>
    </form>
  {/if}
</section>

<style>
  .settings {
    display: grid;
    gap: var(--sc-gap);
  }
  .intro {
    margin: 0;
    color: var(--secondary-text-color);
  }
  form {
    display: grid;
    gap: var(--sc-gap);
  }
  fieldset {
    display: grid;
    gap: 12px;
    margin: 0;
    min-width: 0;
  }
  legend {
    font-weight: 700;
    padding: 0 6px;
  }
  .live,
  .hint {
    margin: 0;
    color: var(--secondary-text-color);
    font-size: 0.875rem;
  }
  .radios {
    display: flex;
    flex-wrap: wrap;
    gap: 4px 20px;
  }
  .radio,
  .check {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    min-height: 44px;
  }
  .radio input,
  .check input {
    width: 20px;
    height: 20px;
  }
  .notice {
    margin: 0;
    padding: 10px 12px;
    border-radius: 8px;
    border: 1px solid currentColor;
  }
  .notice p {
    margin: 0;
  }
  .notice ul {
    margin: 6px 0 0;
    padding-left: 20px;
  }
  .notice.error {
    color: var(--error-color);
  }
  .notice.ok {
    color: var(--success-color);
  }
  .loc {
    font-weight: 600;
  }
  .actions {
    display: flex;
    justify-content: flex-end;
  }
</style>
