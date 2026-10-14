<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import { ApiError } from '../api/client';
  import type { EntityCandidate, ValidationIssue } from '../api/types';
  import {
    candidateValueText,
    criterionKeys,
    filterCandidates,
    resolveCandidate,
  } from '../entityFormat';
  import { t, tDynamic } from '../i18n/index.svelte';
  import { loadCandidates } from '../stores/candidates';
  import type { RefField } from '../wizard/forms';
  import FieldIssues from './FieldIssues.svelte';
  import UnitBadge from './UnitBadge.svelte';

  interface Props {
    label: string;
    field: RefField;
    /** Site role (`grid_power`) or, with `template`, a role of that template. */
    role: string;
    template?: string;
    /** Scopes a `same_device_as` role to the registry device of the primary entity. */
    deviceId?: string | null;
    /** Ranks candidates of a device with this name first (does not filter). */
    deviceName?: string | null;
    /** Show the sign switch. */
    signed?: boolean;
    hint?: string;
    optional?: boolean;
    issues?: readonly ValidationIssue[];
    /** Called with the candidate that matches the field (or `null`) whenever it changes. */
    oncandidate?: (candidate: EntityCandidate | null) => void;
    onremove?: () => void;
  }
  let {
    label,
    field = $bindable(),
    role,
    template,
    deviceId,
    deviceName,
    signed = false,
    hint,
    optional = false,
    issues = [],
    oncandidate,
    onremove,
  }: Props = $props();

  const uid = $props.id();
  const listId = `${uid}-list`;
  const optionId = (i: number) => `${uid}-opt-${i}`;

  let candidates = $state.raw<EntityCandidate[]>([]);
  let loadedOnce = $state(false);
  let loadError = $state<string | null>(null);
  let open = $state(false);
  let active = $state(-1);
  // While the list is open the typed text filters; opening on focus shows everything.
  let filtering = $state(false);
  // Diagnostic, hidden and config entities are left out by default; this asks for them too.
  let includeHidden = $state(false);

  $effect(() => {
    const query = { role, template, deviceId, deviceName, includeHidden };
    let stale = false;
    loadCandidates(query).then(
      (list) => {
        if (stale) return;
        candidates = list;
        loadError = null;
        loadedOnce = true;
      },
      (e: unknown) => {
        if (stale) return;
        loadError = e instanceof ApiError ? (e.key ?? 'api.error.network') : 'api.error.network';
        loadedOnce = true;
      },
    );
    return () => {
      stale = true;
    };
  });

  const candidate = $derived(resolveCandidate(candidates, field.entity_id));
  const notFound = $derived(
    candidate === undefined && field.entity_id.trim() !== '' && loadedOnce && loadError === null,
  );
  const suggestions = $derived(filterCandidates(candidates, filtering ? field.entity_id : ''));
  const best = $derived(candidates[0]);
  const showBest = $derived(field.entity_id.trim() === '' && best !== undefined && best.score > 0);

  let lastReported: string | null | undefined;
  $effect(() => {
    const id = candidate?.entity_id ?? null;
    if (id === lastReported) return;
    lastReported = id;
    oncandidate?.(candidate ?? null);
  });

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

  const where = (c: EntityCandidate): string =>
    [c.area_name, c.device_name].filter((x): x is string => !!x).join(' · ');
</script>

<div class="field" data-testid="role-picker">
  <label for="{uid}-id">
    {label}
    {#if optional}<span class="opt">{t('field.optional')}</span>{/if}
  </label>
  {#if hint}<p class="hint" id="{uid}-hint">{hint}</p>{/if}
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
        aria-invalid={issues.length > 0 ? 'true' : undefined}
        aria-describedby={[hint ? `${uid}-hint` : '', issues.length > 0 ? `${uid}-err` : '']
          .filter(Boolean)
          .join(' ') || undefined}
        bind:value={field.entity_id}
        oninput={onInput}
        onfocus={show}
        onclick={show}
        onblur={onBlur}
        onkeydown={onKeydown}
        autocomplete="off"
        autocapitalize="off"
        spellcheck="false"
        placeholder={t('picker.placeholder')}
      />
      <ul
        id={listId}
        class="listbox"
        role="listbox"
        aria-label={t('picker.suggestions')}
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
            data-entity={c.entity_id}
            onmousedown={(e) => e.preventDefault()}
            onclick={() => select(c)}
          >
            <span class="name">
              {c.name}
              {#if i === 0 && c.score > 0 && !filtering}<span class="best">{t('picker.best')}</span
                >{/if}
            </span>
            <span class="value">{candidateValueText(c)}</span>
            <span class="eid">{c.entity_id}{where(c) ? ` · ${where(c)}` : ''}</span>
            {#if c.criteria.length > 0}
              <span class="chips" aria-label={t('picker.matches')}>
                {#each criterionKeys(c) as key (key)}
                  <span class="chip">{tDynamic(key, undefined, 'picker.criterion_unknown')}</span>
                {/each}
              </span>
            {/if}
          </li>
        {:else}
          <li class="empty" role="presentation">
            {loadedOnce ? t('picker.empty') : t('picker.loading')}
          </li>
        {/each}
        {#if suggestions.total > suggestions.items.length}
          <li class="empty" role="presentation">
            {t('picker.more', { shown: suggestions.items.length, total: suggestions.total })}
          </li>
        {/if}
      </ul>
    </div>
    {#if onremove}
      <button type="button" class="btn secondary" onclick={onremove}>{t('action.remove')}</button>
    {/if}
  </div>

  {#if showBest && best}
    <div class="best-row">
      <button type="button" class="btn secondary small" onclick={() => select(best)}>
        {t('picker.use_best', { name: best.name })}
      </button>
    </div>
  {/if}

  <label class="check hidden-toggle">
    <input type="checkbox" bind:checked={includeHidden} data-testid="include-hidden" />
    {t('picker.include_hidden')}
  </label>

  <div class="meta">
    {#if signed}
      <label class="check">
        <input type="checkbox" bind:checked={field.invert_sign} />
        {t('picker.invert')}
      </label>
    {/if}
    <span class="picked" data-testid="preview">
      {#if loadError}
        {tDynamic(loadError, undefined, 'api.error.unknown')}
      {:else if candidate}
        <strong>{candidate.name}</strong>
        <span class="state">{candidateValueText(candidate)}</span>
        <UnitBadge unit={candidate.unit} />
        {#if candidate.quality !== 'ok'}
          <span class="quality"
            >{tDynamic(`quality.${candidate.quality}`, undefined, 'reason.unknown')}</span
          >
        {/if}
      {:else if notFound}
        {t('picker.not_in_list')}
      {:else if field.entity_id.trim() === ''}
        {optional ? t('picker.none_optional') : t('picker.none')}
      {/if}
    </span>
  </div>
  <FieldIssues {issues} id="{uid}-err" />
</div>

<style>
  .field {
    display: grid;
    gap: 6px;
    min-width: 0;
  }
  label {
    font-weight: 600;
  }
  .opt {
    font-weight: 400;
    color: var(--secondary-text-color);
    font-size: 0.875rem;
    margin-inline-start: 6px;
  }
  .hint {
    margin: 0;
    color: var(--secondary-text-color);
    font-size: 0.875rem;
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
    max-height: min(60vh, 360px);
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
    row-gap: 2px;
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
  .best {
    margin-inline-start: 8px;
    padding: 0 8px;
    border-radius: 10px;
    background: var(--primary-color);
    color: var(--text-primary-color);
    font-size: 0.75rem;
    font-weight: 600;
    white-space: nowrap;
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
  .chips {
    grid-column: 1 / -1;
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
  }
  .chip {
    padding: 0 8px;
    border-radius: 10px;
    border: 1px solid var(--divider-color);
    font-size: 0.75rem;
    color: var(--secondary-text-color);
  }
  .empty {
    padding: 10px;
    color: var(--secondary-text-color);
    font-size: 0.875rem;
  }
  .small {
    min-height: 36px;
    padding: 0 14px;
  }
  .meta {
    display: flex;
    flex-wrap: wrap;
    gap: 4px 20px;
    align-items: center;
    color: var(--secondary-text-color);
    font-size: 0.875rem;
  }
  .picked {
    display: inline-flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 6px 10px;
  }
  .picked strong {
    color: var(--primary-text-color);
  }
  .state {
    font-variant-numeric: tabular-nums;
    color: var(--primary-text-color);
  }
  .quality {
    color: var(--warning-color);
    font-weight: 600;
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
