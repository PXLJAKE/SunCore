<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import { api } from '../api/client';
  import { describeError } from '../api/errors';
  import type { Diagnostics } from '../api/types';
  import {
    DIAG_SECTIONS,
    exportBytes,
    exportFileName,
    exportText,
    sectionCount,
    sectionText,
    type DiagSection,
  } from '../diagnostics/present';
  import { t, tDateTime, tDynamic, tNumber, type MessageKey } from '../i18n/index.svelte';
  import Disclosure from '../ui/Disclosure.svelte';
  import Icon from '../ui/Icon.svelte';

  const uid = $props.id();

  let doc = $state.raw<Diagnostics | null>(null);
  let busy = $state(false);
  let error = $state<string | null>(null);
  let previewOpen = $state(false);
  let saved = $state<string | null>(null);
  const closed = (): Record<DiagSection, boolean> =>
    Object.fromEntries(DIAG_SECTIONS.map((s) => [s, false])) as Record<DiagSection, boolean>;
  let opened = $state(closed());

  const SECTION_LABEL: Record<DiagSection, MessageKey> = {
    versions: 'diag.section.versions',
    architecture: 'diag.section.architecture',
    connection: 'diag.section.connection',
    watchdog: 'diag.section.watchdog',
    planner: 'diag.section.planner',
    timeseries: 'diag.section.timeseries',
    configuration: 'diag.section.configuration',
    mapped_entities: 'diag.section.mapped_entities',
    decisions: 'diag.section.decisions',
    logs: 'diag.section.logs',
  };

  /** Fetch a new export (a new salt: the pseudonyms change with every export). */
  async function load(): Promise<Diagnostics | null> {
    busy = true;
    error = null;
    saved = null;
    try {
      doc = await api.diagnostics();
      opened = closed();
      return doc;
    } catch (e) {
      error = describeError(e).key;
      return null;
    } finally {
      busy = false;
    }
  }

  // The preview loads when it is opened for the first time.
  $effect(() => {
    if (previewOpen && doc === null && !busy && error === null) void load();
  });

  /** Save exactly what the preview shows, so what a person read is what they post. */
  async function download(): Promise<void> {
    const out = doc ?? (await load());
    if (!out) return;
    const blob = new Blob([exportText(out)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = exportFileName(out);
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 0);
    saved = exportFileName(out);
  }

  const kb = $derived(doc ? tNumber(exportBytes(doc) / 1024, { maximumFractionDigits: 0 }) : '');
</script>

<section class="card stack" aria-labelledby="{uid}-t" data-testid="diagnostics">
  <h4 id="{uid}-t">{t('diag.title')}</h4>
  <p class="hint">{t('diag.hint')}</p>
  <p class="notice warn" data-testid="diag-privacy">{t('diag.privacy')}</p>

  <div class="row">
    <button
      type="button"
      class="btn secondary"
      disabled={busy}
      data-testid="diag-download"
      onclick={() => void download()}><Icon name="download" size={18} />{t('diag.download')}</button
    >
    {#if doc}
      <button
        type="button"
        class="btn secondary"
        disabled={busy}
        data-testid="diag-refresh"
        onclick={() => void load()}>{t('diag.refresh')}</button
      >
    {/if}
  </div>

  {#if saved}
    <p class="notice ok" role="status" data-testid="diag-saved">
      {t('diag.saved', { file: saved })}
    </p>
  {/if}
  {#if error}
    <p class="notice error" role="alert" data-testid="diag-error">
      {tDynamic(error, undefined, 'api.error.unknown')}
    </p>
  {/if}

  <Disclosure summary={t('diag.preview')} bind:open={previewOpen} testid="diag-preview">
    {#if busy && !doc}
      <p class="hint" role="status">{t('diag.loading')}</p>
    {:else if doc}
      <dl class="facts" data-testid="diag-facts">
        <div>
          <dt>{t('diag.generated')}</dt>
          <dd>{tDateTime(new Date(doc.generated_at))}</dd>
        </div>
        <div>
          <dt>{t('diag.version')}</dt>
          <dd>{doc.versions.app}</dd>
        </div>
        <div>
          <dt>{t('diag.size')}</dt>
          <dd>{t('diag.size_value', { kb })}</dd>
        </div>
      </dl>
      {#if doc.unavailable.length > 0}
        <p class="notice warn" data-testid="diag-unavailable">
          {t('diag.unavailable', { list: doc.unavailable.join(', ') })}
        </p>
      {/if}
      <ul class="sections">
        {#each DIAG_SECTIONS as section (section)}
          {@const count = sectionCount(doc, section)}
          <li data-testid="diag-section-{section}">
            <Disclosure
              summary={count === null
                ? t(SECTION_LABEL[section])
                : t('diag.section_count', { name: t(SECTION_LABEL[section]), count })}
              bind:open={opened[section]}
            >
              {#if opened[section]}
                <!-- A scroll area must be reachable by keyboard. -->
                <!-- svelte-ignore a11y_no_noninteractive_tabindex -->
                <pre class="json" tabindex="0" aria-label={t(SECTION_LABEL[section])}>{sectionText(
                    doc,
                    section,
                  )}</pre>
              {/if}
            </Disclosure>
          </li>
        {/each}
      </ul>
    {/if}
  </Disclosure>
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
  .row {
    display: flex;
    flex-wrap: wrap;
    gap: var(--sc-space-2);
  }
  .sections {
    display: grid;
    gap: var(--sc-space-1);
    margin: var(--sc-space-2) 0 0;
    padding: 0;
    list-style: none;
  }
  .json {
    margin: 0;
    max-height: 20rem;
    overflow: auto;
    padding: var(--sc-space-3);
    border: 1px solid var(--sc-outline);
    border-radius: var(--sc-radius-md);
    background: var(--sc-surface-sunken);
    color: var(--sc-text);
    font-size: var(--sc-font-xs);
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }
  .json:focus-visible {
    outline: 3px solid var(--sc-accent);
    outline-offset: 2px;
  }
</style>
