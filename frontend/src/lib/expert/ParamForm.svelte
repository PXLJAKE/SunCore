<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import { onMount } from 'svelte';
  import { paramsApi } from '../api/client';
  import { describeError } from '../api/errors';
  import type { Param, ParamValue, ParamsApplied, ValidationIssue } from '../api/types';
  import ConfirmDialog from '../components/ConfirmDialog.svelte';
  import { hasMessage, loadParamCatalogue, t, tDynamic, tNumber } from '../i18n/index.svelte';
  import ParamField from './ParamField.svelte';
  import {
    groupParams,
    isEdited,
    issuesFor,
    matchesQuery,
    needsRestart,
    planChanges,
    safetyChanges,
    type Drafts,
    type Raw,
  } from './model';

  const uid = $props.id();

  let params = $state.raw<Param[]>([]);
  let groups = $state.raw<string[]>([]);
  let loaded = $state(false);
  let loadError = $state<string | null>(null);
  let drafts = $state.raw<Drafts>({});
  let query = $state('');
  let onlyModified = $state(false);
  let busy = $state(false);
  let headline = $state<string | null>(null);
  let serverIssues = $state.raw<ValidationIssue[]>([]);
  let notice = $state<string | null>(null);
  let restartHint = $state(false);
  let confirming = $state(false);

  function apply(res: { params: Param[]; groups: string[] }): void {
    params = res.params;
    groups = res.groups;
  }

  onMount(async () => {
    try {
      const [listing] = await Promise.all([paramsApi.list(), loadParamCatalogue()]);
      apply(listing);
    } catch (e) {
      loadError = describeError(e).key;
    } finally {
      loaded = true;
    }
  });

  const labelOf = (p: Param): string =>
    hasMessage(p.name_key) ? tDynamic(p.name_key, undefined) : p.key;
  const groupLabel = (g: string): string =>
    tDynamic(`param.group.${g}`, undefined, 'expert.group_unknown');

  const visibleGroups = $derived(
    groupParams(
      params,
      groups,
      (p) =>
        (!onlyModified || p.modified || isEdited(p, drafts)) &&
        matchesQuery(
          query,
          p.key,
          labelOf(p),
          hasMessage(p.help_key) ? tDynamic(p.help_key, undefined) : '',
        ),
    ),
  );
  const plan = $derived(planChanges(params, drafts));
  const pending = $derived(Object.keys(plan.changes).length);
  const hasErrors = $derived(Object.keys(plan.errors).length > 0);
  const safety = $derived(safetyChanges(params, plan.changes));
  const anyDraft = $derived(Object.keys(drafts).length > 0);

  function edit(p: Param, raw: Raw): void {
    drafts = { ...drafts, [p.key]: raw };
    notice = null;
  }

  function dropDraft(key: string): void {
    const { [key]: _gone, ...rest } = drafts;
    drafts = rest;
  }

  function discard(): void {
    drafts = {};
    headline = null;
    serverIssues = [];
  }

  function fail(e: unknown): void {
    const f = describeError(e);
    headline = f.key;
    serverIssues = f.issues;
  }

  function succeed(res: ParamsApplied, message: string): void {
    apply(res);
    restartHint = needsRestart(res.params, res.changed);
    notice = message;
    headline = null;
    serverIssues = [];
  }

  function save(): void {
    if (pending === 0 || hasErrors) return;
    if (safety.length > 0) confirming = true;
    else void send(false);
  }

  async function send(confirmed: boolean): Promise<void> {
    busy = true;
    try {
      const changes = plan.changes;
      const res = await paramsApi.put(changes, confirmed);
      for (const k of Object.keys(changes)) dropDraft(k);
      succeed(res, t('expert.saved', { count: res.changed.length }));
    } catch (e) {
      fail(e);
    } finally {
      busy = false;
      confirming = false;
    }
  }

  async function resetKey(p: Param): Promise<void> {
    busy = true;
    try {
      const res = await paramsApi.resetKey(p.key);
      dropDraft(p.key);
      succeed(res, t('expert.reset_done', { count: res.changed.length }));
    } catch (e) {
      fail(e);
    } finally {
      busy = false;
    }
  }

  async function resetGroup(group: string): Promise<void> {
    busy = true;
    try {
      const res = await paramsApi.resetGroup(group);
      for (const p of params.filter((x) => x.group === group)) dropDraft(p.key);
      succeed(res, t('expert.reset_done', { count: res.changed.length }));
    } catch (e) {
      fail(e);
    } finally {
      busy = false;
    }
  }

  function show(v: ParamValue): string {
    if (v === null || v === undefined || v === '') return t('expert.empty_value');
    if (typeof v === 'boolean') return v ? t('expert.on') : t('expert.off');
    if (typeof v === 'number') return tNumber(v, { maximumFractionDigits: 6 });
    return v;
  }

  // Issues without a field of their own (e.g. a key the page does not show).
  const orphanIssues = $derived(
    serverIssues.filter((i) => !params.some((p) => issuesFor(p.key, [i]).length > 0)),
  );
</script>

<div class="stack" data-testid="param-form">
  {#if !loaded}
    <p class="hint" role="status">{t('expert.loading')}</p>
  {:else if loadError}
    <p class="notice error" role="alert">{tDynamic(loadError, undefined, 'api.error.unknown')}</p>
  {:else}
    <div class="tools">
      <div class="field grow">
        <label for="{uid}-search">{t('expert.search')}</label>
        <input
          id="{uid}-search"
          class="input"
          type="search"
          bind:value={query}
          data-testid="param-search"
        />
      </div>
      <label class="check">
        <input type="checkbox" bind:checked={onlyModified} data-testid="param-only-modified" />
        <span>{t('expert.only_modified')}</span>
      </label>
    </div>

    <div aria-live="polite">
      {#if headline}
        <div class="notice error" role="alert" data-testid="param-error">
          <p>{tDynamic(headline, undefined, 'api.error.unknown')}</p>
          {#if orphanIssues.length > 0}
            <ul>
              {#each orphanIssues as issue, i (i)}
                <li>{tDynamic(issue.key, issue.args, 'api.validation.invalid')}</li>
              {/each}
            </ul>
          {/if}
        </div>
      {:else if notice}
        <p class="notice ok" role="status" data-testid="param-notice">{notice}</p>
      {/if}
      {#if restartHint}
        <p class="notice warn" role="status" data-testid="restart-hint">
          {t('expert.restart_hint')}
        </p>
      {/if}
    </div>

    {#each visibleGroups as group (group.name)}
      <section class="card group" aria-labelledby="{uid}-g-{group.name}" data-group={group.name}>
        <div class="group-head">
          <h4 id="{uid}-g-{group.name}">{groupLabel(group.name)}</h4>
          <button
            type="button"
            class="btn secondary small"
            disabled={busy || group.modified === 0}
            data-testid="group-reset"
            onclick={() => void resetGroup(group.name)}
            aria-label={t('expert.reset_group_aria', { group: groupLabel(group.name) })}
            >{t('expert.reset_group')}</button
          >
        </div>
        {#each group.params as p (p.key)}
          <ParamField
            param={p}
            draft={drafts[p.key]}
            issues={[
              ...(plan.errors[p.key] ? [plan.errors[p.key] as ValidationIssue] : []),
              ...issuesFor(p.key, serverIssues),
            ]}
            {busy}
            onedit={(raw) => edit(p, raw)}
            onreset={() => (p.modified ? void resetKey(p) : dropDraft(p.key))}
          />
        {/each}
      </section>
    {:else}
      <p class="hint" data-testid="param-none">{t('expert.none')}</p>
    {/each}

    <div class="savebar">
      <span class="hint" role="status">{t('expert.pending', { count: pending })}</span>
      <button
        type="button"
        class="btn secondary"
        disabled={busy || !anyDraft}
        onclick={discard}
        data-testid="param-discard">{t('expert.discard')}</button
      >
      <button
        type="button"
        class="btn"
        disabled={busy || pending === 0 || hasErrors}
        onclick={save}
        data-testid="param-save">{t('expert.save')}</button
      >
    </div>
  {/if}
</div>

<ConfirmDialog
  open={confirming}
  title={t('expert.confirm.title')}
  confirmLabel={t('expert.confirm.action')}
  danger
  {busy}
  testid="safety-confirm"
  onconfirm={() => void send(true)}
  oncancel={() => (confirming = false)}
>
  <p>{t('expert.confirm.text')}</p>
  <ul data-testid="safety-list">
    {#each safety as c (c.key)}
      <li>
        <strong
          >{hasMessage(`param.${c.key}.name`)
            ? tDynamic(`param.${c.key}.name`, undefined)
            : c.key}</strong
        >:
        {t('expert.confirm.item', { from: show(c.from), to: show(c.to) })}
      </li>
    {/each}
  </ul>
</ConfirmDialog>

<style>
  .tools {
    display: flex;
    flex-wrap: wrap;
    align-items: end;
    gap: 8px 20px;
  }
  .grow {
    flex: 1 1 14rem;
  }
  .group {
    display: grid;
    gap: 0;
  }
  .group-head {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    margin-bottom: 4px;
  }
  .group-head h4 {
    font-size: 1.0625rem;
  }
  .savebar {
    position: sticky;
    bottom: 0;
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: flex-end;
    gap: 8px 12px;
    padding: 10px 0;
    background: var(--primary-background-color);
  }
  .savebar .hint {
    margin-right: auto;
  }
</style>
