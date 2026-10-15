<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import { onMount } from 'svelte';
  import DecisionList from '../components/DecisionList.svelte';
  import { nameMap } from '../control/present';
  import { t, tDynamic, type MessageKey } from '../i18n/index.svelte';
  import { DECISION_KINDS, DecisionLogStore, LOG_LIMIT } from '../stores/decisionLog.svelte';
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

  const devices = $derived(live.control?.devices ?? []);
  const names = $derived(nameMap(devices));

  onMount(() => void log.load());

  // Reload when a filter changes and when the live feed reports a new decision.
  const newest = $derived(
    live.decisions[0]
      ? `${live.decisions[0].intent.id}${live.decisions[0].ts}${live.decisions[0].kind}`
      : '',
  );
  let first = true;
  $effect(() => {
    void log.deviceId;
    void log.kinds;
    void newest;
    if (first) {
      first = false;
      return;
    }
    void log.load();
  });
</script>

<section aria-labelledby="{uid}-title" class="log" data-testid="decision-log">
  <h2 id="{uid}-title">{t('log.title')}</h2>
  <p class="hint">{t('log.intro', { limit: LOG_LIMIT })}</p>

  <form class="card filters" onsubmit={(e) => e.preventDefault()}>
    <div class="field">
      <label for="{uid}-device">{t('log.device')}</label>
      <select id="{uid}-device" class="input" bind:value={log.deviceId} data-testid="filter-device">
        <option value="">{t('log.all_devices')}</option>
        {#each devices as d (d.device_id)}
          <option value={d.device_id}>{d.name}</option>
        {/each}
      </select>
    </div>
    <fieldset>
      <legend>{t('log.kind')}</legend>
      <div class="kinds">
        {#each DECISION_KINDS as k (k)}
          <label class="check">
            <input
              type="checkbox"
              checked={log.kinds.includes(k)}
              onchange={() => log.toggleKind(k)}
              data-testid="filter-kind-{k}"
            />
            {t(KIND[k])}
          </label>
        {/each}
      </div>
      <p class="hint">{t('log.kind_hint')}</p>
    </fieldset>
  </form>

  {#if log.error}
    <p class="notice error" role="alert">{tDynamic(log.error, undefined, 'api.error.unknown')}</p>
  {/if}

  <div class="card" aria-busy={log.loading} data-testid="log-list">
    <p class="count" role="status">{t('log.count', { count: log.items.length })}</p>
    <DecisionList decisions={log.items} {names} limit={LOG_LIMIT} withDate />
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
    grid-template-columns: repeat(auto-fit, minmax(14rem, 1fr));
    align-items: start;
  }
  fieldset {
    margin: 0;
    padding: 0;
    border: 0;
    min-width: 0;
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
  .count {
    margin: 0 0 8px;
    color: var(--secondary-text-color);
    font-size: 0.875rem;
  }
</style>
