<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import type { EntityCandidate } from '../api/types';
  import { t, tPercent, tPower } from '../i18n/index.svelte';
  import { previewOf, type PreviewKind, type RefField } from '../siteForm';

  interface Props {
    label: string;
    field: RefField;
    candidates: EntityCandidate[];
    kind?: PreviewKind;
    /** `id` of the shared <datalist> holding the entity options. */
    listId: string;
    /** Hide the sign switch (state of charge has no sign). */
    signed?: boolean;
    /** Removable row (PV list). */
    onremove?: () => void;
  }
  let {
    label,
    field = $bindable(),
    candidates,
    kind = 'power',
    listId,
    signed = true,
    onremove,
  }: Props = $props();

  const uid = $props.id();
  const candidate = $derived(candidates.find((c) => c.entity_id === field.entity_id.trim()));
  const preview = $derived(previewOf(candidate, field.invert_sign, kind));
</script>

<div class="field">
  <label for="{uid}-id">{label}</label>
  <div class="row">
    <input
      id="{uid}-id"
      class="input"
      list={listId}
      bind:value={field.entity_id}
      autocomplete="off"
      autocapitalize="off"
      spellcheck="false"
      placeholder={t('settings.entity.placeholder')}
    />
    {#if onremove}
      <button type="button" class="btn secondary" onclick={onremove}>{t('settings.remove')}</button>
    {/if}
  </div>
  <div class="meta">
    {#if signed}
      <label class="check">
        <input type="checkbox" bind:checked={field.invert_sign} />
        {t('settings.invert')}
      </label>
    {/if}
    <span class="preview" data-testid="preview">
      {#if preview.type === 'none'}
        {t('settings.preview.none')}
      {:else if preview.type === 'unavailable'}
        {t('settings.preview.unavailable')}
      {:else if preview.type === 'power'}
        {t('settings.preview.value', { value: tPower(preview.watts) })}
      {:else if preview.type === 'percent'}
        {t('settings.preview.value', { value: tPercent(preview.percent) })}
      {:else}
        {t('settings.preview.raw', { value: preview.text })}
      {/if}
    </span>
  </div>
</div>

<style>
  .field {
    display: grid;
    gap: 6px;
  }
  label {
    font-weight: 600;
  }
  .row {
    display: flex;
    gap: 8px;
  }
  .meta {
    display: flex;
    flex-wrap: wrap;
    gap: 6px 20px;
    align-items: center;
    color: var(--secondary-text-color);
    font-size: 0.875rem;
  }
  .check {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    min-height: 44px;
    font-weight: 400;
    color: var(--primary-text-color);
  }
  .check input {
    width: 20px;
    height: 20px;
  }
</style>
