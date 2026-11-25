<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import { api } from '../../api/client';
  import { describeError } from '../../api/errors';
  import type { ConfigHistoryEntry, ConfigSectionName } from '../../api/types';
  import { t, tDateTime, tDynamic } from '../../i18n/index.svelte';
  import { app } from '../../stores/app.svelte';
  import { clearCandidateCache } from '../../stores/candidates';
  import { expert } from '../../stores/expert.svelte';
  import { wizard } from '../../stores/wizard.svelte';
  import Icon from '../../ui/Icon.svelte';

  /** Where the project lives (`addon/suncore/config.yaml`, `url`). */
  const SOURCE_URL = 'https://github.com/pxljake/suncore';

  const uid = $props.id();
  type HistorySection = Exclude<
    ConfigSectionName,
    'wizard' | 'mqtt' | 'notifications' | 'inverter'
  >;
  const SECTIONS: HistorySection[] = [
    'site',
    'region',
    'tariff',
    'forecast',
    'battery',
    'devices',
    'goals',
    'operation',
  ];

  // --- history / restore -------------------------------------------------------------
  let histSection = $state<HistorySection>('site');
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

  const info = $derived(app.info);
</script>

<!-- Expert mode: a per-browser switch (it moved here from the top bar in 7-G). -->
<section class="card switch-row" aria-labelledby="{uid}-ex">
  <div class="text">
    <h4 id="{uid}-ex">{t('expert.toggle')}</h4>
    <p class="hint" id="{uid}-ex-hint">{t('settings.expert.hint')}</p>
  </div>
  <button
    type="button"
    role="switch"
    class="switch"
    aria-checked={expert.enabled}
    aria-describedby="{uid}-ex-hint"
    data-testid="expert-toggle"
    onclick={() => expert.set(!expert.enabled)}
  >
    <span class="sr-only">{t('expert.toggle')}</span>
    <span class="track" aria-hidden="true"></span>
  </button>
</section>

{#if expert.enabled}
  {#await import('../../expert/ExpertArea.svelte') then area}
    <area.default />
  {/await}
{:else}
  <p class="hint" data-testid="expert-off">{t('settings.expert.off')}</p>
{/if}

<!-- 7-I: the diagnostics panel (GET /api/v1/diagnostics) goes here, between expert mode and the
     configuration history. -->

<section class="card stack" aria-labelledby="{uid}-hist">
  <h4 id="{uid}-hist">{t('settings.history.title')}</h4>
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
  <h4 id="{uid}-bak">{t('settings.backup.title')}</h4>
  <p class="hint">{t('settings.backup.hint')}</p>
  <div class="row">
    <a
      class="btn secondary"
      href={api.exportUrl()}
      download="suncore-config.json"
      data-testid="export"><Icon name="download" size={18} />{t('settings.export')}</a
    >
    <label class="btn secondary file">
      {t('settings.import')}
      <input
        type="file"
        accept="application/json,.json"
        class="file-input"
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

<section class="card stack" aria-labelledby="{uid}-about" data-testid="about">
  <h4 id="{uid}-about">{t('settings.about.title')}</h4>
  <dl class="facts">
    <div>
      <dt>{t('settings.about.version')}</dt>
      <dd data-testid="about-version">{info?.version ?? '–'}</dd>
    </div>
    <div>
      <dt>{t('settings.about.data')}</dt>
      <dd>{info?.demo ? t('settings.about.demo') : t('settings.about.live')}</dd>
    </div>
    <div>
      <dt>{t('settings.about.licence')}</dt>
      <dd>{t('settings.about.licence_name')}</dd>
    </div>
  </dl>
  <p>
    <a href={SOURCE_URL} target="_blank" rel="noopener noreferrer"
      >{t('settings.about.source')}<span class="sr-only"> {t('settings.about.new_tab')}</span></a
    >
  </p>
</section>

<style>
  h4 {
    margin: 0;
    font-size: var(--sc-font-md);
    font-weight: var(--sc-weight-strong);
  }
  p {
    margin: 0;
  }
  .switch-row {
    display: flex;
    align-items: center;
    gap: var(--sc-space-4);
  }
  .text {
    display: grid;
    flex: 1;
    gap: var(--sc-space-1);
    min-width: 0;
  }
  .switch {
    display: inline-grid;
    place-items: center;
    flex: none;
    min-width: var(--sc-target);
    min-height: var(--sc-target);
    padding: 0;
    border: 0;
    background: none;
    cursor: pointer;
  }
  .track {
    position: relative;
    width: 44px;
    height: 26px;
    border-radius: var(--sc-radius-pill);
    background: var(--sc-outline-strong);
    transition: background-color var(--sc-motion-fast) ease;
  }
  .track::after {
    content: '';
    position: absolute;
    top: 3px;
    left: 3px;
    width: 20px;
    height: 20px;
    border-radius: 50%;
    background: var(--sc-surface);
    box-shadow: var(--sc-elev-1);
    transition: transform var(--sc-motion-fast) ease;
  }
  .switch[aria-checked='true'] .track {
    background: var(--sc-accent);
  }
  .switch[aria-checked='true'] .track::after {
    transform: translateX(18px);
  }
  .row {
    display: flex;
    flex-wrap: wrap;
    gap: var(--sc-space-2);
    align-items: end;
  }
  .history {
    display: grid;
    gap: var(--sc-space-1);
    margin: 0;
    padding: 0;
    list-style: none;
  }
  .entry {
    display: flex;
    flex-wrap: wrap;
    gap: var(--sc-space-2);
    align-items: center;
  }
  .entry span {
    flex: 1;
    min-width: 12ch;
  }
  .file {
    position: relative;
    cursor: pointer;
  }
  .file:focus-within {
    outline: 3px solid var(--sc-accent);
    outline-offset: 2px;
  }
  .file-input {
    position: absolute;
    inset: 0;
    width: 100%;
    opacity: 0;
    cursor: pointer;
  }
</style>
