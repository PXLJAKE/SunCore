<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import { onMount } from 'svelte';
  import { api } from '../api/client';
  import { describeError } from '../api/errors';
  import type { TemplateSummary } from '../api/types';
  import { t, tDynamic } from '../i18n/index.svelte';

  interface Props {
    onpick: (template: TemplateSummary) => void;
    oncancel: () => void;
  }
  let { onpick, oncancel }: Props = $props();

  const uid = $props.id();
  let templates = $state.raw<TemplateSummary[]>([]);
  let loading = $state(true);
  let error = $state<string | null>(null);
  let query = $state('');
  let kind = $state('');

  const kinds = $derived(
    [...new Set(templates.map((x) => x.device_kind ?? ''))].filter(Boolean).sort(),
  );
  const shown = $derived(
    templates.filter((x) => {
      if (kind !== '' && x.device_kind !== kind) return false;
      const hay = [
        tDynamic(x.name, undefined, 'reason.unknown'),
        x.id,
        x.device_kind ?? '',
        ...x.capabilities,
      ]
        .join(' ')
        .toLowerCase();
      return query
        .toLowerCase()
        .split(/\s+/)
        .filter(Boolean)
        .every((term) => hay.includes(term));
    }),
  );

  onMount(() => {
    void (async () => {
      try {
        templates = (await api.templates('device')).templates;
      } catch (e) {
        error = describeError(e).key;
      } finally {
        loading = false;
      }
    })();
  });
</script>

<section class="gallery card stack" aria-labelledby="{uid}-title" data-testid="gallery">
  <h3 id="{uid}-title">{t('gallery.title')}</h3>
  <div class="cols">
    <div class="field">
      <label for="{uid}-q">{t('gallery.search')}</label>
      <input id="{uid}-q" class="input" type="search" bind:value={query} autocomplete="off" />
    </div>
    <div class="field">
      <label for="{uid}-kind">{t('gallery.kind')}</label>
      <select id="{uid}-kind" class="input" bind:value={kind}>
        <option value="">{t('gallery.kind_all')}</option>
        {#each kinds as k (k)}
          <option value={k}>{tDynamic(`device.kind.${k}`, undefined, 'device.kind.generic')}</option
          >
        {/each}
      </select>
    </div>
  </div>

  {#if loading}
    <p class="hint" role="status">{t('gallery.loading')}</p>
  {:else if error}
    <p class="notice error" role="alert">{tDynamic(error, undefined, 'api.error.unknown')}</p>
  {:else}
    <p class="hint" role="status">{t('gallery.count', { count: shown.length })}</p>
    <ul class="tiles">
      {#each shown as tpl (tpl.id)}
        <li class="tile">
          <h4>{tDynamic(tpl.name, undefined, 'reason.unknown')}</h4>
          <p class="meta">
            <span class="chip-tag"
              >{tDynamic(
                `device.kind.${tpl.device_kind ?? 'generic'}`,
                undefined,
                'device.kind.generic',
              )}</span
            >
            {#if tpl.origin === 'user'}<span class="chip-tag">{t('gallery.user')}</span>{/if}
            {#if tpl.unverified.length > 0 && tpl.tested_with.length === 0}
              <span class="chip-tag warn">{t('template.untested')}</span>
            {/if}
          </p>
          <p class="hint">
            {tpl.capabilities
              .map((c) => tDynamic(`capability.${c}`, undefined, 'capability.unknown'))
              .join(' · ')}
          </p>
          <button type="button" class="btn small" onclick={() => onpick(tpl)}>
            {t('gallery.pick')}
            <span class="sr-only">{tDynamic(tpl.name, undefined, 'reason.unknown')}</span>
          </button>
        </li>
      {:else}
        <li class="hint">{t('gallery.empty')}</li>
      {/each}
    </ul>
  {/if}
  <div>
    <button type="button" class="btn secondary" onclick={oncancel}>{t('action.cancel')}</button>
  </div>
</section>

<style>
  .tiles {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(15rem, 1fr));
    gap: 12px;
    margin: 0;
    padding: 0;
    list-style: none;
  }
  .tile {
    display: grid;
    gap: 6px;
    align-content: start;
    padding: 12px;
    border: 1px solid var(--divider-color);
    border-radius: 10px;
    background: var(--card-background-color);
  }
  .tile h4 {
    margin: 0;
    font-size: 1rem;
  }
  .meta {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    margin: 0;
  }
  .tile .btn {
    justify-self: start;
  }
</style>
