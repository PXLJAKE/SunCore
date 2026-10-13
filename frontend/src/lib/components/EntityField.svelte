<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import type { EntityCandidate } from '../api/types';
  import { t, tPercent, tPower } from '../i18n/index.svelte';
  import {
    filterCandidates,
    previewOf,
    resolveCandidate,
    type PreviewKind,
    type RefField,
  } from '../siteForm';

  interface Props {
    label: string;
    field: RefField;
    candidates: EntityCandidate[];
    kind?: PreviewKind;
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
    signed = true,
    onremove,
  }: Props = $props();

  const uid = $props.id();
  const listId = `${uid}-list`;
  const optionId = (i: number) => `${uid}-opt-${i}`;

  let open = $state(false);
  let active = $state(-1);
  // While the list is open the typed text filters; opening on focus shows everything of the kind.
  let filtering = $state(false);

  const candidate = $derived(resolveCandidate(candidates, field.entity_id));
  const preview = $derived(previewOf(candidate, field.invert_sign, kind));
  const notFound = $derived(
    candidate === undefined && field.entity_id.trim() !== '' && candidates.length > 0,
  );
  const suggestions = $derived(
    filterCandidates(candidates, kind, filtering ? field.entity_id : ''),
  );

  function valueText(c: EntityCandidate): string {
    const p = previewOf(c, false, kind);
    if (p.type === 'power') return tPower(p.watts);
    if (p.type === 'percent') return tPercent(p.percent);
    if (p.type === 'raw') return p.text;
    return t('settings.entity.unavailable');
  }

  function show(): void {
    open = true;
  }
  function close(): void {
    open = false;
    active = -1;
    filtering = false;
  }
  function select(c: EntityCandidate): void {
    field.entity_id = c.entity_id;
    close();
  }
  function onInput(): void {
    filtering = true;
    active = -1;
    show();
  }
  function onBlur(): void {
    close();
    // An exact friendly name is stored as the entity id, so saving never sends a display name.
    const c = resolveCandidate(candidates, field.entity_id);
    if (c && c.entity_id !== field.entity_id.trim()) field.entity_id = c.entity_id;
  }
  function move(delta: number): void {
    const n = suggestions.items.length;
    if (n === 0) return;
    show();
    active = active < 0 ? (delta > 0 ? 0 : n - 1) : (active + delta + n) % n;
    queueMicrotask(() =>
      document.getElementById(optionId(active))?.scrollIntoView?.({ block: 'nearest' }),
    );
  }
  function onKeydown(e: KeyboardEvent): void {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      move(1);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      move(-1);
    } else if (e.key === 'Enter') {
      const c = open ? suggestions.items[active] : undefined;
      if (c) {
        e.preventDefault();
        select(c);
      }
    } else if (e.key === 'Escape' && open) {
      e.preventDefault();
      e.stopPropagation();
      close();
    }
  }
</script>

<div class="field">
  <label for="{uid}-id">{label}</label>
  <div class="row">
    <div class="combo">
      <input
        id="{uid}-id"
        class="input"
        role="combobox"
        aria-expanded={open}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={open && active >= 0 ? optionId(active) : undefined}
        bind:value={field.entity_id}
        oninput={onInput}
        onfocus={show}
        onclick={show}
        onblur={onBlur}
        onkeydown={onKeydown}
        autocomplete="off"
        autocapitalize="off"
        spellcheck="false"
        placeholder={t('settings.entity.placeholder')}
      />
      <ul
        id={listId}
        class="listbox"
        role="listbox"
        aria-label={t('settings.entity.suggestions')}
        hidden={!open}
      >
        {#each suggestions.items as c, i (c.entity_id)}
          <!-- svelte-ignore a11y_click_events_have_key_events -->
          <!-- Keyboard use goes through the combobox input (APG pattern); options are pointer targets. -->
          <li
            id={optionId(i)}
            class="option"
            class:active={i === active}
            role="option"
            aria-selected={i === active}
            onmousedown={(e) => e.preventDefault()}
            onclick={() => select(c)}
          >
            <span class="name">{c.name}</span>
            <span class="value">{valueText(c)}</span>
            <span class="eid">{c.entity_id}</span>
          </li>
        {:else}
          <li class="empty" role="presentation">{t('settings.entity.empty')}</li>
        {/each}
        {#if suggestions.total > suggestions.items.length}
          <li class="empty" role="presentation">
            {t('settings.entity.more', {
              shown: suggestions.items.length,
              total: suggestions.total,
            })}
          </li>
        {/if}
      </ul>
    </div>
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
      {#if notFound}
        {t(kind === 'soc' ? 'settings.preview.not_found_soc' : 'settings.preview.not_found_power')}
      {:else if preview.type === 'none'}
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
  .combo {
    position: relative;
    flex: 1;
    min-width: 0;
  }
  .listbox {
    position: absolute;
    z-index: 20;
    inset-inline: 0;
    top: calc(100% + 4px);
    max-height: min(60vh, 320px);
    overflow-y: auto;
    margin: 0;
    padding: 4px;
    list-style: none;
    border: 1px solid var(--secondary-text-color);
    border-radius: 8px;
    background: var(--card-background-color);
    box-shadow: 0 6px 20px rgb(0 0 0 / 0.25);
  }
  .listbox[hidden] {
    display: none;
  }
  .option {
    display: grid;
    grid-template-columns: 1fr auto;
    column-gap: 12px;
    align-items: center;
    min-height: 44px;
    padding: 6px 10px;
    border-radius: 6px;
    cursor: pointer;
    color: var(--primary-text-color);
  }
  .option:hover,
  .option.active {
    background: color-mix(in srgb, var(--primary-color) 18%, transparent);
  }
  .option.active {
    outline: 2px solid var(--primary-color);
    outline-offset: -2px;
  }
  .name {
    font-weight: 600;
    overflow-wrap: anywhere;
  }
  .value {
    font-variant-numeric: tabular-nums;
  }
  .eid {
    grid-column: 1 / -1;
    font-size: 0.8125rem;
    color: var(--secondary-text-color);
    overflow-wrap: anywhere;
  }
  .empty {
    padding: 10px;
    color: var(--secondary-text-color);
    font-size: 0.875rem;
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
