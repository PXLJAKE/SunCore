<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import { onMount } from 'svelte';
  import { api } from '../api/client';
  import { describeError } from '../api/errors';
  import type { ConfigHistoryEntry, ConfigSectionName } from '../api/types';
  import { t, tDateTime, tDynamic, tNumber, type MessageKey } from '../i18n/index.svelte';
  import { router, stepHash } from '../router.svelte';
  import { clearCandidateCache } from '../stores/candidates';
  import { app } from '../stores/app.svelte';
  import { wizard } from '../stores/wizard.svelte';
  import { STEPS, type StepId } from '../wizard/steps';

  const uid = $props.id();
  // The battery section gets its settings page in Phase 4 WP10.
  const SECTIONS: Exclude<ConfigSectionName, 'wizard' | 'battery'>[] = [
    'site',
    'region',
    'tariff',
    'forecast',
    'devices',
    'goals',
    'operation',
  ];

  // Reload to pick up changes made elsewhere, but never over unsaved edits of the assistant.
  onMount(() => {
    if (wizard.loaded && wizard.dirty().length > 0) return;
    wizard.invalidate();
    void wizard.ensureLoaded();
  });

  const unsaved = $derived(wizard.dirty().length > 0);

  async function discard(): Promise<void> {
    await wizard.discard();
  }

  let restarting = $state(false);
  let restartError = $state<string | null>(null);

  /** "Run setup again": the steps count as not done again, the configuration stays. */
  async function rerun(): Promise<void> {
    restarting = true;
    restartError = null;
    try {
      await wizard.restart();
      router.go(stepHash('region'));
    } catch (e) {
      restartError = describeError(e).key;
    } finally {
      restarting = false;
    }
  }

  const s = $derived(wizard.saved);
  const rows = $derived.by((): { step: StepId; text: string }[] => {
    const site = s.site;
    const pvKwp =
      site && (site.pv ?? []).length > 0 && (site.pv ?? []).every((a) => a.kwp != null)
        ? (site.pv ?? []).reduce((sum, a) => sum + (a.kwp ?? 0), 0)
        : null;
    const tariff = s.tariff;
    return [
      {
        step: 'region',
        text: s.region
          ? t('settings.sum.region', {
              region: s.region.region_id,
              currency: s.region.currency,
              tz: s.region.timezone,
            })
          : t('settings.sum.none'),
      },
      { step: 'energy', text: t('settings.sum.energy') },
      {
        step: 'grid',
        text: site
          ? t('settings.sum.grid', {
              kind: site.grid.power ? 'single' : 'pair',
              entity: site.grid.power?.entity_id ?? site.grid.import_power?.entity_id ?? '',
            })
          : t('settings.sum.none'),
      },
      {
        step: 'pv',
        text: site
          ? pvKwp == null
            ? t('settings.sum.pv', { count: (site.pv ?? []).length })
            : t('settings.sum.pv_kwp', {
                count: (site.pv ?? []).length,
                kwp: tNumber(pvKwp, { maximumFractionDigits: 2 }),
              })
          : t('settings.sum.none'),
      },
      {
        step: 'battery',
        text: site
          ? site.battery
            ? t('settings.sum.battery', { entity: site.battery.power.entity_id })
            : t('settings.sum.battery_none')
          : t('settings.sum.none'),
      },
      {
        step: 'tariff',
        text: tariff
          ? t('settings.sum.tariff', { imp: tariff.import.kind, exp: tariff.export.kind })
          : t('settings.sum.none'),
      },
      {
        step: 'devices',
        text: t('settings.sum.devices', { count: s.devices?.devices?.length ?? 0 }),
      },
      {
        step: 'priorities',
        text: s.goals ? t('settings.sum.goals') : t('settings.sum.none'),
      },
      {
        step: 'mode',
        text: s.operation ? t(`mode.${s.operation.mode}`) : t('settings.sum.mode_default'),
      },
    ];
  });
  const titleOf = (id: StepId): MessageKey => STEPS.find((x) => x.id === id)!.title;

  // --- history / restore -------------------------------------------------------------
  let histSection = $state<Exclude<ConfigSectionName, 'wizard'>>('site');
  let history = $state.raw<ConfigHistoryEntry[] | null>(null);
  let histError = $state<string | null>(null);
  let confirming = $state<number | null>(null);
  let restored = $state<string | null>(null);

  async function loadHistory(): Promise<void> {
    history = null;
    histError = null;
    confirming = null;
    try {
      history = await api.history(histSection);
    } catch (e) {
      histError = describeError(e).key;
    }
  }

  async function restore(entry: ConfigHistoryEntry): Promise<void> {
    try {
      await api.restore(entry.section as ConfigSectionName, entry.id);
      restored = t('settings.history.restored');
      confirming = null;
      clearCandidateCache();
      await app.load();
      wizard.invalidate();
      await wizard.ensureLoaded();
      await loadHistory();
    } catch (e) {
      histError = describeError(e).key;
    }
  }

  // --- export / import -------------------------------------------------------------------
  let importMsg = $state<{ kind: 'ok' | 'error'; text: string } | null>(null);

  async function onFile(e: Event & { currentTarget: HTMLInputElement }): Promise<void> {
    const file = e.currentTarget.files?.[0];
    e.currentTarget.value = '';
    if (!file) return;
    try {
      const res = await api.importConfig(await file.text());
      importMsg = {
        kind: 'ok',
        text: t('settings.import.done', {
          count: res.imported_sections.length,
          sections: res.imported_sections.join(', '),
        }),
      };
      await app.load();
      wizard.invalidate();
      await wizard.ensureLoaded();
    } catch (err) {
      importMsg = {
        kind: 'error',
        text: tDynamic(describeError(err).key, undefined, 'api.error.unknown'),
      };
    }
  }
</script>

<section class="settings" aria-labelledby="{uid}-title">
  <h2 id="{uid}-title">{t('settings.title')}</h2>
  <p class="intro">{t('settings.intro')}</p>

  {#if wizard.loadError}
    <p class="notice error" role="alert">
      {tDynamic(wizard.loadError, undefined, 'api.error.unknown')}
    </p>
  {/if}

  {#if unsaved}
    <div class="notice warn" role="status" data-testid="unsaved-notice">
      <p>{t('settings.unsaved')}</p>
      <div class="row">
        <a class="btn small" href="#/setup">{t('settings.unsaved.resume')}</a>
        <button
          type="button"
          class="btn secondary small"
          data-testid="unsaved-discard"
          onclick={() => void discard()}>{t('settings.unsaved.discard')}</button
        >
      </div>
    </div>
  {/if}

  <div class="card stack" data-testid="wizard-status">
    <h3>{t('settings.wizard.title')}</h3>
    <p>
      {wizard.completed ? t('settings.wizard.done') : t('settings.wizard.open')}
    </p>
    {#if restartError}
      <p class="notice error" role="alert">
        {tDynamic(restartError, undefined, 'api.error.unknown')}
      </p>
    {/if}
    <div class="row">
      {#if wizard.completed}
        <button
          type="button"
          class="btn"
          data-testid="open-wizard"
          disabled={restarting}
          onclick={() => void rerun()}>{t('settings.wizard.rerun')}</button
        >
      {:else}
        <a class="btn" href="#/setup" data-testid="open-wizard">{t('settings.wizard.continue')}</a>
      {/if}
    </div>
  </div>

  <ul class="overview" data-testid="overview">
    {#each rows as row (row.step)}
      <li class="card item" data-step={row.step}>
        <div>
          <h3>{t(titleOf(row.step))}</h3>
          <p class="hint">{row.text}</p>
        </div>
        <a class="btn secondary small" href={stepHash(row.step)}>
          {t('action.edit')}<span class="sr-only"> {t(titleOf(row.step))}</span>
        </a>
      </li>
    {/each}
    <li class="card item" data-step="summary">
      <div>
        <h3>{t(titleOf('summary'))}</h3>
        <p class="hint">{t('settings.sum.check')}</p>
      </div>
      <a class="btn secondary small" href={stepHash('summary')}
        >{t('settings.open')}<span class="sr-only"> {t(titleOf('summary'))}</span></a
      >
    </li>
  </ul>

  <section class="card stack" aria-labelledby="{uid}-hist">
    <h3 id="{uid}-hist">{t('settings.history.title')}</h3>
    <p class="hint">{t('settings.history.hint')}</p>
    <div class="row">
      <div class="field">
        <label for="{uid}-sec">{t('settings.history.section')}</label>
        <select id="{uid}-sec" class="input" bind:value={histSection}>
          {#each SECTIONS as sec (sec)}
            <option value={sec}>{t(`settings.section.${sec}`)}</option>
          {/each}
        </select>
      </div>
      <button type="button" class="btn secondary" onclick={() => void loadHistory()}
        >{t('settings.history.show')}</button
      >
    </div>
    {#if restored}<p class="notice ok" role="status">{restored}</p>{/if}
    {#if histError}
      <p class="notice error" role="alert">{tDynamic(histError, undefined, 'api.error.unknown')}</p>
    {/if}
    {#if history}
      <ul class="history" data-testid="history">
        {#each history as entry (entry.id)}
          <li class="entry">
            <span>{tDateTime(new Date(entry.replaced_at))}</span>
            {#if confirming === entry.id}
              <button type="button" class="btn danger small" onclick={() => void restore(entry)}
                >{t('settings.history.confirm')}</button
              >
              <button type="button" class="btn secondary small" onclick={() => (confirming = null)}
                >{t('action.cancel')}</button
              >
            {:else}
              <button
                type="button"
                class="btn secondary small"
                onclick={() => (confirming = entry.id)}
                >{t('settings.history.restore')}<span class="sr-only">
                  {tDateTime(new Date(entry.replaced_at))}</span
                ></button
              >
            {/if}
          </li>
        {:else}
          <li class="hint">{t('settings.history.empty')}</li>
        {/each}
      </ul>
    {/if}
  </section>

  <section class="card stack" aria-labelledby="{uid}-bak">
    <h3 id="{uid}-bak">{t('settings.backup.title')}</h3>
    <p class="hint">{t('settings.backup.hint')}</p>
    <div class="row">
      <a
        class="btn secondary"
        href={api.exportUrl()}
        download="suncore-config.json"
        data-testid="export">{t('settings.export')}</a
      >
      <label class="btn secondary file">
        {t('settings.import')}
        <input
          type="file"
          accept="application/json,.json"
          class="sr-only-input"
          data-testid="import-file"
          onchange={(e) => void onFile(e)}
        />
      </label>
    </div>
    {#if importMsg}
      <p
        class="notice {importMsg.kind}"
        role={importMsg.kind === 'error' ? 'alert' : 'status'}
        data-testid="import-result"
      >
        {importMsg.text}
      </p>
    {/if}
  </section>
</section>

<style>
  .settings {
    display: grid;
    gap: var(--sc-gap);
  }
  .notice p {
    margin: 0 0 8px;
    color: var(--primary-text-color);
  }
  .intro {
    margin: 0;
    color: var(--secondary-text-color);
  }
  .overview {
    display: grid;
    gap: 8px;
    margin: 0;
    padding: 0;
    list-style: none;
  }
  .item {
    display: flex;
    flex-wrap: wrap;
    gap: 8px 16px;
    align-items: center;
    justify-content: space-between;
  }
  .item h3,
  .item p {
    margin: 0;
  }
  .row {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    align-items: end;
  }
  .history {
    display: grid;
    gap: 6px;
    margin: 0;
    padding: 0;
    list-style: none;
  }
  .entry {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    align-items: center;
  }
  .entry span {
    flex: 1;
    min-width: 12ch;
  }
  .file {
    position: relative;
    cursor: pointer;
    display: inline-flex;
    align-items: center;
  }
  .file:focus-within {
    outline: 3px solid var(--primary-color);
    outline-offset: 2px;
  }
  .sr-only-input {
    position: absolute;
    inset: 0;
    opacity: 0;
    cursor: pointer;
    width: 100%;
  }
</style>
