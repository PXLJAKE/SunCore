<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import { onMount, untrack } from 'svelte';
  import { controlApi } from '../api/client';
  import DecisionTable from '../components/DecisionTable.svelte';
  import { nameMap, splitSharedReasons } from '../control/present';
  import { tReason } from '../control/reasonText';
  import Card from '../ui/Card.svelte';
  import Chip from '../ui/Chip.svelte';
  import EmptyState from '../ui/EmptyState.svelte';
  import Icon from '../ui/Icon.svelte';
  import { t, tDynamic, tNumber, type MessageKey } from '../i18n/index.svelte';
  import {
    DECISION_KINDS,
    DECISION_LAYERS,
    emptyFilters,
    filterCount,
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
  // A note every listed entry carries (shadow mode: nothing switched) is said once, above.
  const split = $derived(splitSharedReasons(log.items));
  const activeFilters = $derived(filterCount(draft));
  // The filters fill a phone screen: there they start closed unless a link sets some.
  const wide = (): boolean => {
    try {
      return window.matchMedia('(min-width: 600px)').matches;
    } catch {
      return true;
    }
  };
  let filtersOpen = $state(wide() || hasFilters(filtersFromParams(router.query)));

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
  <header class="head">
    <h2 id="{uid}-title">{t('log.title')}</h2>
    <p class="hint intro">{t('log.intro')}</p>
  </header>

  <Card
    title={t('log.filters')}
    icon="filter"
    collapsible
    bind:open={filtersOpen}
    density="compact"
    testid="log-filter-card"
  >
    {#snippet status()}
      {#if activeFilters > 0}
        <Chip tone="accent" testid="log-filter-count"
          >{t('log.filters.active', { count: activeFilters })}</Chip
        >
      {/if}
    {/snippet}
    <form class="filters" onsubmit={(e) => e.preventDefault()} data-testid="log-filters">
      <div class="field">
        <label for="{uid}-device">{t('log.device')}</label>
        <select
          id="{uid}-device"
          class="input"
          bind:value={draft.device}
          data-testid="filter-device"
        >
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
          class="btn secondary small"
          onclick={reset}
          disabled={!hasFilters(draft)}
          data-testid="filter-reset">{t('log.reset')}</button
        >
      </div>
    </form>
  </Card>

  {#if log.error}
    <p class="notice error" role="alert">{tDynamic(log.error, undefined, 'api.error.unknown')}</p>
  {/if}

  <div class="card list" aria-busy={log.loading} data-testid="log-list">
    <div class="list-head">
      <p class="count" role="status" data-testid="log-count">
        {#if log.total === 0}
          {t('log.count', { count: 0 })}
        {:else}
          {t('log.range', { from: range.from, to: range.to, total: log.total })}
        {/if}
      </p>
      <a class="btn secondary small" href={csv} download data-testid="log-export"
        ><Icon name="download" size={16} />{t('log.export')}</a
      >
    </div>
    {#if log.items.length === 0}
      <EmptyState
        icon="analysis"
        title={t(hasFilters(log.filters) ? 'log.empty_filtered' : 'decisions.empty')}
      />
    {:else}
      {#if split.shared.length > 0}
        <p class="shared" data-testid="log-shared">
          <Icon name="info" size={16} />
          <span>{split.shared.map((r) => tReason(r)).join(' ')}</span>
        </p>
      {/if}
      <DecisionTable
        decisions={log.items}
        ids={log.ids}
        reasons={split.own}
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
    gap: var(--sc-space-5);
    min-width: 0;
  }
  .head {
    display: grid;
    gap: var(--sc-space-2);
  }
  .intro {
    max-width: 48rem;
  }
  .filters {
    display: grid;
    gap: var(--sc-space-3) var(--sc-space-5);
    grid-template-columns: repeat(auto-fit, minmax(min(100%, 13rem), 1fr));
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
    font-weight: var(--sc-weight-strong);
    font-size: var(--sc-font-sm);
    padding: 0;
    margin-bottom: var(--sc-space-1);
  }
  .kinds {
    display: flex;
    flex-wrap: wrap;
    gap: 0 var(--sc-space-4);
  }
  .actions {
    grid-column: 1 / -1;
    display: flex;
    flex-wrap: wrap;
    gap: var(--sc-space-2);
  }
  .list {
    display: grid;
    gap: var(--sc-space-3);
    min-width: 0;
  }
  .list-head {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: var(--sc-space-2) var(--sc-space-3);
  }
  .count {
    margin: 0;
    color: var(--sc-text-muted);
    font-size: var(--sc-font-sm);
    font-variant-numeric: tabular-nums;
  }
  .shared {
    display: flex;
    align-items: flex-start;
    gap: var(--sc-space-2);
    margin: 0;
    padding: var(--sc-space-2) var(--sc-space-3);
    border-radius: var(--sc-radius-md);
    background: var(--sc-info-bg);
    font-size: var(--sc-font-sm);
  }
  .shared :global(.icon) {
    flex: none;
    margin-top: 2px;
    color: var(--sc-info);
  }
  .err {
    color: var(--sc-error);
    margin: var(--sc-space-1) 0 0;
    font-size: var(--sc-font-sm);
  }
  .pager {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--sc-space-3);
    font-variant-numeric: tabular-nums;
  }
</style>
