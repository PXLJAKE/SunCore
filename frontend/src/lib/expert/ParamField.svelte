<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import type { Param, ParamValue, ValidationIssue } from '../api/types';
  import FieldIssues from '../components/FieldIssues.svelte';
  import { hasMessage, t, tDynamic, tNumber } from '../i18n/index.svelte';
  import type { Raw } from './model';

  interface Props {
    param: Param;
    /** The edited input, `undefined` while untouched. */
    draft: Raw | undefined;
    issues: readonly ValidationIssue[];
    onedit: (raw: Raw) => void;
    onreset: () => void;
    busy?: boolean;
  }
  let { param, draft, issues, onedit, onreset, busy = false }: Props = $props();

  const uid = $props.id();
  const name = $derived(
    hasMessage(param.name_key) ? tDynamic(param.name_key, undefined) : param.key,
  );
  const help = $derived(hasMessage(param.help_key) ? tDynamic(param.help_key, undefined) : '');
  const current = $derived<Raw>(
    draft ?? (param.type === 'bool' ? param.value === true : String(param.value ?? '')),
  );
  const edited = $derived(draft !== undefined);

  function show(v: ParamValue): string {
    if (v === null || v === undefined || v === '') return t('expert.empty_value');
    if (typeof v === 'boolean') return v ? t('expert.on') : t('expert.off');
    if (typeof v === 'number') return tNumber(v, { maximumFractionDigits: 6 });
    return v;
  }
  const unit = $derived(param.unit ? ` ${param.unit}` : '');
  const range = $derived(
    param.minimum !== null || param.maximum !== null
      ? t('expert.range', {
          min: param.minimum === null ? '–' : tNumber(param.minimum, { maximumFractionDigits: 6 }),
          max: param.maximum === null ? '–' : tNumber(param.maximum, { maximumFractionDigits: 6 }),
        }) + unit
      : '',
  );
  const describedBy = $derived(
    [`${uid}-help`, issues.length > 0 ? `${uid}-err` : ''].filter(Boolean).join(' '),
  );
  const disabled = $derived(!param.available || busy);
</script>

<div
  class="param"
  class:modified={param.modified || edited}
  data-key={param.key}
  data-testid="param"
>
  <div class="head">
    <label for="{uid}-in" class="name">{name}</label>
    {#if param.modified}<span class="chip-tag" data-testid="modified-marker"
        >{t('expert.modified')}</span
      >{/if}
    {#if edited}<span class="chip-tag warn">{t('expert.unsaved')}</span>{/if}
    {#if param.safety}<span class="chip-tag warn">{t('expert.tag.safety')}</span>{/if}
    {#if param.restart}<span class="chip-tag">{t('expert.tag.restart')}</span>{/if}
    <code class="key">{param.key}</code>
  </div>

  {#if param.type === 'bool'}
    <label class="check">
      <input
        id="{uid}-in"
        type="checkbox"
        {disabled}
        checked={current === true}
        aria-describedby={describedBy}
        onchange={(e) => onedit(e.currentTarget.checked)}
      />
      <span>{current === true ? t('expert.on') : t('expert.off')}</span>
    </label>
  {:else if param.type === 'enum'}
    <select
      id="{uid}-in"
      class="input"
      {disabled}
      value={String(current)}
      aria-invalid={issues.length > 0 ? 'true' : undefined}
      aria-describedby={describedBy}
      onchange={(e) => onedit(e.currentTarget.value)}
    >
      {#each param.choices as choice (choice)}
        <option value={choice}>{choice}</option>
      {/each}
    </select>
  {:else}
    <div class="with-unit">
      <input
        id="{uid}-in"
        class="input"
        type={param.type === 'entity' ? 'text' : 'number'}
        inputmode={param.type === 'entity' ? 'text' : 'decimal'}
        min={param.minimum ?? undefined}
        max={param.maximum ?? undefined}
        step={param.type === 'int' ? 1 : 'any'}
        {disabled}
        value={String(current)}
        aria-invalid={issues.length > 0 ? 'true' : undefined}
        aria-describedby={describedBy}
        oninput={(e) => onedit(e.currentTarget.value)}
      />
      {#if param.unit}<span class="unit" aria-hidden="true">{param.unit}</span>{/if}
    </div>
  {/if}

  <p class="hint" id="{uid}-help">
    {#if help}{help}<br />{/if}
    {t('expert.default', {
      value: show(param.default) + (typeof param.default === 'number' ? unit : ''),
    })}
    {#if range}· {range}{/if}
    {#if param.see}· {t('expert.see', { see: param.see })}{/if}
    {#if !param.available}<br />{t('expert.unavailable')}{/if}
  </p>
  <FieldIssues {issues} id="{uid}-err" />

  <div class="row">
    <button
      type="button"
      class="btn secondary small"
      disabled={busy || (!param.modified && !edited)}
      data-testid="param-reset"
      onclick={onreset}
      aria-label={t('expert.reset_aria', { name })}>{t('expert.reset')}</button
    >
  </div>
</div>

<style>
  .param {
    display: grid;
    gap: 6px;
    padding: 12px 0;
    border-top: 1px solid var(--divider-color);
    min-width: 0;
  }
  .param.modified {
    border-left: 3px solid var(--primary-color);
    padding-left: 10px;
  }
  .head {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 6px 8px;
  }
  .name {
    font-weight: 600;
  }
  .key {
    margin-left: auto;
    font-size: 0.75rem;
    color: var(--secondary-text-color);
    overflow-wrap: anywhere;
  }
  .with-unit {
    display: flex;
    align-items: center;
    gap: 8px;
    max-width: 22rem;
  }
  .unit {
    color: var(--secondary-text-color);
    white-space: nowrap;
  }
  select.input {
    max-width: 22rem;
  }
  .row {
    display: flex;
    gap: 8px;
  }
</style>
