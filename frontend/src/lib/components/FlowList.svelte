<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import type { Flows, FlowValue } from '../api/types';
  import { activityOf, isUsable, type Activity, type NodeId } from '../flow';
  import { t, tPercent, tPower, type MessageKey } from '../i18n/index.svelte';

  interface Props {
    flows: Flows | null;
    hasBattery: boolean;
  }
  let { flows, hasBattery }: Props = $props();

  const ACTIVITY: Record<Activity, MessageKey> = {
    idle: 'activity.idle',
    importing: 'activity.importing',
    exporting: 'activity.exporting',
    charging: 'activity.charging',
    discharging: 'activity.discharging',
    producing: 'activity.producing',
    consuming: 'activity.consuming',
  };
  const LABEL: Record<NodeId, MessageKey> = {
    pv: 'node.pv',
    grid: 'node.grid',
    battery: 'node.battery',
    house: 'node.house',
  };
  const QUALITY: Record<'stale' | 'unavailable' | 'invalid', MessageKey> = {
    stale: 'quality.stale',
    unavailable: 'quality.unavailable',
    invalid: 'quality.invalid',
  };
  const DASH = '–';

  const unknown: FlowValue = { value: null, quality: 'unavailable' };

  const rows = $derived.by(() => {
    const ids: NodeId[] = hasBattery ? ['pv', 'grid', 'battery', 'house'] : ['pv', 'grid', 'house'];
    return ids.map((id) => {
      const f: FlowValue = flows
        ? { pv: flows.pv_w, grid: flows.grid_w, battery: flows.battery_w, house: flows.house_w }[id]
        : unknown;
      const soc = id === 'battery' && flows ? flows.battery_soc_pct : null;
      return {
        id,
        quality: f.quality,
        value: isUsable(f) ? tPower(Math.abs(f.value)) : DASH,
        state: f.quality === 'ok' ? t(ACTIVITY[activityOf(id, f)]) : t(QUALITY[f.quality]),
        soc: soc && isUsable(soc) ? tPercent(soc.value) : null,
      };
    });
  });
</script>

<ul class="list" aria-label={t('now.list.label')}>
  {#each rows as r (r.id)}
    <li class="row" data-testid="flow-{r.id}" data-quality={r.quality}>
      <span class="name">{t(LABEL[r.id])}</span>
      <span class="val">{r.value}</span>
      <span class="state" class:warn={r.quality !== 'ok'}>{r.state}</span>
      {#if r.soc}
        <span class="soc">{t('now.soc', { soc: r.soc })}</span>
      {/if}
    </li>
  {/each}
</ul>

<style>
  .list {
    list-style: none;
    margin: 0;
    padding: 0;
    display: grid;
    gap: 8px;
    grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  }
  .row {
    display: grid;
    gap: 2px;
    padding: 10px 12px;
    border: 1px solid var(--divider-color);
    border-radius: 10px;
    background: var(--card-background-color);
  }
  .name {
    color: var(--secondary-text-color);
    font-size: 0.875rem;
  }
  .val {
    font-size: 1.25rem;
    font-weight: 600;
  }
  .state,
  .soc {
    font-size: 0.875rem;
    color: var(--secondary-text-color);
  }
  .state.warn {
    color: var(--error-color);
    font-weight: 600;
  }
  .row[data-quality='stale'] .state.warn {
    color: var(--warning-color);
  }
</style>
