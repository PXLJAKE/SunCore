<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import { onMount, untrack } from 'svelte';
  import { controlApi } from '../api/client';
  import DecisionTable from '../components/DecisionTable.svelte';
  import { nameMap } from '../control/present';
  import { t, tDynamic, tNumber, type MessageKey } from '../i18n/index.svelte';
  import {
    DECISION_KINDS,
    DECISION_LAYERS,
    emptyFilters,
    filterProblems,
    filtersFromParams,
    filtersToParams,
    hasFilters,
    logHash,
    type LogFilters,
  } from '../log/filters';
  import { router } from '../router.svelte';
  import { DecisionLogStore } from '../stores/decisionLog.svelte';
  import { live } from '../stores/live.svelte';

  const uid = $props.id();
  const log = new DecisionLogStore();
  const KIND: Record<(typeof DECISION_KINDS)[number], MessageKey> = {
    accepted: 'decision.kind.accepted',
    rejected: 'decision.kind.rejected',
    shadow: 'decision.kind.shadow',
    proposed: 'decision.kind.proposed',
    held: 'decision.kind.held',
    failed: 'decision.kind.failed',
  };
  const LAYER: Record<(typeof DECISION_LAYERS)[number], MessageKey> = {
    planner: 'log.layer.planner',
    controller: 'log.layer.controller',
    user: 'log.layer.user',
    safety: 'log.layer.safety',
  };

  const devices = $derived(live.control?.devices ?? []);
  const names = $derived(nameMap(devices, [['battery', t('battery.title')]]));
  const cards = $derived(new Set(devices.map((d) => d.device_id)));

  /** What the form shows; it reaches the URL (and with it the server) a moment after typing. */
  let draft = $state<LogFilters>(emptyFilters());
  let open = $state<number | null>(null);

  const urlKey = $derived(filtersToParams(filtersFromParams(router.query)).toString());
  const problems = $derived(filterProblems(draft));
  const range = $derived({
    from: log.total === 0 ? 0 : log.offset + 1,
    to: log.offset + log.items.length,
  });
  const csv = $derived(controlApi.decisionsCsvUrl(log.exportParams()));

  onMount(() => () => clearTimeout(timer));

  // URL -> store and form: a pasted or shared link, the browser's back button.
  $effect(() => {
    const f = filtersFromParams(new URLSearchParams(urlKey));
    untrack(() => {
      log.setFilters(f);
      draft = { ...f, kinds: [...f.kinds] };
      open = null;
      void log.load();
    });
  });

  // Form -> URL, debounced for typing; nothing is sent while a field is invalid.
  let timer: ReturnType<typeof setTimeout> | undefined;
  $effect(() => {
    const key = filtersToParams(draft).toString();
    const bad = problems.reason || problems.range;
    clearTimeout(timer);
    if (bad || key === untrack(() => urlKey)) return;
    timer = setTimeout(() => router.replace(logHash(draft)), 250);
  });

  // A new decision on the live feed refreshes the newest page (older pages would shift).
  const newest = $derived(
    live.decisions[0]
      ? `${live.decisions[0].intent.id}${live.decisions[0].ts}${live.decisions[0].kind}`
      : '',
  );
  let first = true;
  $effect(() => {
    void newest;
    if (first) {
      first = false;
      return;
    }
    untrack(() => {
      if (log.offset === 0 && !log.filters.to) void log.load();
    });
  });

  function toggleDraftKind(kind: (typeof DECISION_KINDS)[number]): void {
    draft.kinds = draft.kinds.includes(kind)
      ? draft.kinds.filter((k) => k !== kind)
      : [...draft.kinds, kind];
  }

  async function goTo(page: number): Promise<void> {
    log.goToPage(page);
    open = null;
    await log.load();
  }

  function toggle(id: number): void {
    if (open === id) {
      open = null;
      return;
    }
    open = id;
    void log.explain(id);
  }

  function reset(): void {
    draft = emptyFilters();
  }
</script>

<section aria-labelledby="{uid}-title" class="log" data-testid="decision-log">
  <h2 id="{uid}-title">{t('log.title')}</h2>
  <p class="hint">{t('log.intro')}</p>

  <form class="card filters" onsubmit={(e) => e.preventDefault()} data-testid="log-filters">
    <div class="field">
      <label for="{uid}-device">{t('log.device')}</label>
      <select id="{uid}-device" class="input" bind:value={draft.device} data-testid="filter-device">
        <option value="">{t('log.all_devices')}</option>
        {#each devices as d (d.device_id)}
          <option value={d.device_id}>{d.name}</option>
        {/each}
        {#if draft.device && !cards.has(draft.device)}
          <option value={draft.device}>{names.get(draft.device) ?? draft.device}</option>
        {/if}
      </select>
    </div>
    <div class="field">
      <label for="{uid}-layer">{t('log.layer')}</label>
      <select id="{uid}-layer" class="input" bind:value={draft.layer} data-testid="filter-layer">
        <option value="">{t('log.layer.all')}</option>
        {#each DECISION_LAYERS as l (l)}
          <option value={l}>{t(LAYER[l])}</option>
        {/each}
      </select>
    </div>
    <div class="field">
      <label for="{uid}-from">{t('log.from')}</label>
      <input
        id="{uid}-from"
        class="input"
        type="date"
        bind:value={draft.from}
        data-testid="filter-from"
      />
    </div>
    <div class="field">
      <label for="{uid}-to">{t('log.to')}</label>
      <input
        id="{uid}-to"
        class="input"
        type="date"
        bind:value={draft.to}
        aria-invalid={problems.range}
        aria-describedby={problems.range ? `${uid}-range-err` : undefined}
        data-testid="filter-to"
      />
      {#if problems.range}
        <p class="err" id="{uid}-range-err" role="alert">{t('log.range_invalid')}</p>
      {/if}
    </div>
    <div class="field">
      <label for="{uid}-reason">{t('log.reason')}</label>
      <input
        id="{uid}-reason"
        class="input"
        type="text"
        autocomplete="off"
        spellcheck="false"
        bind:value={draft.reason}
        aria-invalid={problems.reason}
        aria-describedby="{uid}-reason-hint"
        data-testid="filter-reason"
      />
      <p class:err={problems.reason} class="hint" id="{uid}-reason-hint">
        {problems.reason ? t('log.reason_invalid') : t('log.reason_hint')}
      </p>
    </div>
    <div class="field">
      <label for="{uid}-q">{t('log.search')}</label>
      <input
        id="{uid}-q"
        class="input"
        type="search"
        maxlength="100"
        autocomplete="off"
        bind:value={draft.q}
        aria-describedby="{uid}-q-hint"
        data-testid="filter-q"
      />
      <p class="hint" id="{uid}-q-hint">{t('log.search_hint')}</p>
    </div>
    <fieldset>
      <legend>{t('log.kind')}</legend>
      <div class="kinds">
        {#each DECISION_KINDS as k (k)}
          <label class="check">
            <input
              type="checkbox"
              checked={draft.kinds.includes(k)}
              onchange={() => toggleDraftKind(k)}
              data-testid="filter-kind-{k}"
            />
            {t(KIND[k])}
          </label>
        {/each}
      </div>
      <p class="hint">{t('log.kind_hint')}</p>
    </fieldset>
    <div class="actions">
      <button
        type="button"
        class="btn secondary"
        onclick={reset}
        disabled={!hasFilters(draft)}
        data-testid="filter-reset">{t('log.reset')}</button
      >
      <a class="btn secondary" href={csv} download data-testid="log-export">{t('log.export')}</a>
    </div>
  </form>

  {#if log.error}
    <p class="notice error" role="alert">{tDynamic(log.error, undefined, 'api.error.unknown')}</p>
  {/if}

  <div class="card" aria-busy={log.loading} data-testid="log-list">
    <p class="count" role="status" data-testid="log-count">
      {#if log.total === 0}
        {t('log.count', { count: 0 })}
      {:else}
        {t('log.range', { from: range.from, to: range.to, total: log.total })}
      {/if}
    </p>
    {#if log.items.length === 0}
      <p class="empty">{t(hasFilters(log.filters) ? 'log.empty_filtered' : 'decisions.empty')}</p>
    {:else}
      <DecisionTable
        decisions={log.items}
        ids={log.ids}
        {names}
        explained={log.explained}
        {open}
        {cards}
        ontoggle={toggle}
        onretry={(id) => void log.explain(id, true)}
      />
    {/if}
    {#if log.pageCount > 1}
      <nav class="pager" aria-label={t('log.pager')}>
        <button
          type="button"
          class="btn secondary"
          disabled={log.page <= 1}
          onclick={() => void goTo(log.page - 1)}
          data-testid="log-prev">{t('log.prev')}</button
        >
        <span data-testid="log-page"
          >{t('log.page', { page: tNumber(log.page), count: tNumber(log.pageCount) })}</span
        >
        <button
          type="button"
          class="btn secondary"
          disabled={log.page >= log.pageCount}
          onclick={() => void goTo(log.page + 1)}
          data-testid="log-next">{t('log.next')}</button
        >
      </nav>
    {/if}
  </div>
</section>

<style>
  .log {
    display: grid;
    gap: var(--sc-gap);
  }
  .filters {
    display: grid;
    gap: 12px 24px;
    grid-template-columns: repeat(auto-fit, minmax(13rem, 1fr));
    align-items: start;
  }
  fieldset {
    margin: 0;
    padding: 0;
    border: 0;
    min-width: 0;
    grid-column: 1 / -1;
  }
  legend {
    font-weight: 600;
    padding: 0;
    margin-bottom: 4px;
  }
  .kinds {
    display: flex;
    flex-wrap: wrap;
    gap: 0 16px;
  }
  .actions {
    grid-column: 1 / -1;
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
  }
  .actions .btn {
    text-decoration: none;
  }
  .count {
    margin: 0 0 8px;
    color: var(--secondary-text-color);
    font-size: 0.875rem;
  }
  .empty {
    margin: 0;
    color: var(--secondary-text-color);
  }
  .err {
    color: var(--error-color);
    margin: 4px 0 0;
    font-size: 0.875rem;
  }
  .pager {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    margin-top: 12px;
  }
</style>
