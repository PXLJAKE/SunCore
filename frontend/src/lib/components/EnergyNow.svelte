<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import type { DeviceControlState, Flows, FlowValue, SiteControlState } from '../api/types';
  import { allocationSplit, qualityKey } from '../control/present';
  import {
    activityOf,
    clampPercent,
    cycleSeconds,
    directionOf,
    isUsable,
    pvDirection,
    speedLevel,
    type Activity,
    type Direction,
    type NodeId,
  } from '../flow';
  import { t, tPercent, tPower, type MessageKey } from '../i18n/index.svelte';
  import Disclosure from '../ui/Disclosure.svelte';
  import Icon from '../ui/Icon.svelte';
  import type { IconName } from '../ui/icons';

  interface Props {
    flows: Flows | null;
    hasBattery: boolean;
    /** Surplus of the site (null until the control state arrived). */
    site?: SiteControlState | null;
    devices?: readonly DeviceControlState[];
  }
  let { flows, hasBattery, site = null, devices = [] }: Props = $props();

  const LABEL: Record<NodeId, MessageKey> = {
    pv: 'node.pv',
    grid: 'node.grid',
    battery: 'node.battery',
    house: 'node.house',
  };
  const ICON: Record<NodeId, IconName> = {
    pv: 'sun',
    grid: 'grid',
    battery: 'battery',
    house: 'house',
  };
  const ACTIVITY: Record<Activity, MessageKey> = {
    idle: 'activity.idle',
    importing: 'activity.importing',
    exporting: 'activity.exporting',
    charging: 'activity.charging',
    discharging: 'activity.discharging',
    producing: 'activity.producing',
    consuming: 'activity.consuming',
  };
  const QUALITY: Record<'stale' | 'unavailable' | 'invalid', MessageKey> = {
    stale: 'quality.stale',
    unavailable: 'quality.unavailable',
    invalid: 'quality.invalid',
  };
  const DASH = '–';
  const unknown: FlowValue = { value: null, quality: 'unavailable' };

  function flowOf(id: NodeId): FlowValue {
    if (!flows) return unknown;
    return { pv: flows.pv_w, grid: flows.grid_w, battery: flows.battery_w, house: flows.house_w }[
      id
    ];
  }

  /** Energy colour of a node: grid and battery change with the direction of the flow. */
  function tone(id: NodeId, f: FlowValue): string {
    if (id === 'pv') return 'pv';
    if (id === 'house') return 'house';
    const reverse = directionOf(f) === 'reverse';
    if (id === 'grid') return reverse ? 'grid-out' : 'grid-in';
    return reverse ? 'bat-out' : 'bat-in';
  }

  const nodes = $derived(
    (hasBattery
      ? (['pv', 'grid', 'house', 'battery'] as const)
      : (['pv', 'grid', 'house'] as const)
    ).map((id) => {
      const f = flowOf(id);
      return {
        id,
        quality: f.quality,
        tone: tone(id, f),
        value: isUsable(f) ? tPower(Math.abs(f.value)) : DASH,
        state: f.quality === 'ok' ? t(ACTIVITY[activityOf(id, f)]) : t(QUALITY[f.quality]),
      };
    }),
  );

  const soc = $derived(flows?.battery_soc_pct ?? unknown);
  const socPct = $derived(isUsable(soc) ? clampPercent(soc.value) : null);

  /** An edge towards the house: `forward` is source -> house (PV, grid) or house -> battery. */
  function edge(id: 'pv' | 'grid' | 'battery'): {
    id: 'pv' | 'grid' | 'battery';
    dir: Direction;
    level: number;
    tone: string;
    stale: boolean;
  } {
    const f = flowOf(id);
    return {
      id,
      dir: id === 'pv' ? pvDirection(f) : directionOf(f),
      level: speedLevel(f),
      tone: tone(id, f),
      stale: f.quality === 'stale',
    };
  }
  const edges = $derived(
    (hasBattery ? (['pv', 'grid', 'battery'] as const) : (['pv', 'grid'] as const)).map(edge),
  );

  // `free_w` is the available power minus the reserve and the committed draw; below zero there is
  // simply no surplus (the gate clamps it the same way). Import caused by the controlled devices
  // is the deficit, shown next to it.
  const split = $derived(site ? allocationSplit(site.allocated_draw_w, devices) : null);
  const siteBad = $derived(qualityKey(site?.quality));
</script>

<section class="card hero" aria-labelledby="energy-title" data-testid="energy-now">
  <h3 id="energy-title" class="kicker">{t('now.energy.title')}</h3>

  <figure class="flow" aria-label={t('now.diagram.label')}>
    <!-- Lines and arrows are decoration; the list items carry every value and state. -->
    {#each edges as e (e.id)}
      <span
        class="edge e-{e.id} {e.dir}"
        class:moving={e.level > 0}
        class:stale={e.stale}
        style:--edge="var(--sc-energy-{e.tone})"
        style:--cycle="{cycleSeconds(e.level)}s"
        aria-hidden="true"
      ></span>
    {/each}
    <ul class="nodes" aria-label={t('now.list.label')}>
      {#each nodes as n (n.id)}
        <li
          class="node n-{n.id}"
          class:stale={n.quality === 'stale'}
          class:bad={n.quality === 'unavailable' || n.quality === 'invalid'}
          data-testid="flow-{n.id}"
          data-quality={n.quality}
        >
          <span class="badge" style:--tone="var(--sc-energy-{n.tone})"
            ><Icon name={ICON[n.id]} size={22} /></span
          >
          <span class="name">{t(LABEL[n.id])}</span>
          <span class="val" data-testid="flow-value">{n.value}</span>
          <span class="state">{n.state}</span>
          {#if n.id === 'battery'}
            <span class="soc" data-testid="flow-soc">
              {t('now.soc', { soc: socPct === null ? DASH : tPercent(socPct) })}
              <span class="bar" aria-hidden="true"
                ><span class="fill" style:width="{socPct ?? 0}%"></span></span
              >
            </span>
          {/if}
        </li>
      {/each}
    </ul>
  </figure>

  {#if site && split}
    <section class="surplus" aria-labelledby="site-title" data-testid="site-summary">
      <h4 id="site-title" class="kicker">{t('site.title')}</h4>
      {#if siteBad}<p class="chip-tag warn" data-testid="site-quality">{t(siteBad)}</p>{/if}
      <dl class="stats">
        <div>
          <dt>{t('site.free')}</dt>
          <dd data-testid="site-free">
            {site.free_w == null ? t('site.unknown') : tPower(Math.max(0, site.free_w))}
          </dd>
        </div>
        <div>
          <dt>{t('site.deficit')}</dt>
          <dd data-testid="site-deficit" class:bad={site.deficit_w > 0}>
            {tPower(site.deficit_w)}
          </dd>
        </div>
        <div>
          <dt>{t('site.running')}</dt>
          <dd data-testid="site-running">{tPower(split.running)}</dd>
        </div>
        {#if split.wouldRun > 0}
          <div>
            <dt>{t('site.would_run')}</dt>
            <dd data-testid="site-would-run">{tPower(split.wouldRun)}</dd>
          </div>
        {/if}
      </dl>
      {#if split.wouldRun > 0}
        <Disclosure summary={t('site.explain')} testid="site-explain">
          <p class="hint">{t('site.would_run_hint')}</p>
        </Disclosure>
      {/if}
    </section>
  {/if}
</section>

<style>
  .hero {
    display: grid;
    gap: var(--sc-space-4);
    min-width: 0;
  }
  /* --- flow: five columns (node, line, node, line, node), PV above the house --------------- */
  .flow {
    --badge: 40px;
    position: relative;
    display: grid;
    grid-template-columns:
      minmax(0, 1fr) minmax(12px, 0.35fr) minmax(0, 1fr) minmax(12px, 0.35fr)
      minmax(0, 1fr);
    grid-template-rows: auto minmax(20px, auto) auto;
    margin: 0;
    width: 100%;
    max-width: 40rem;
    justify-self: center;
  }
  .nodes {
    display: contents;
    list-style: none;
  }
  .node {
    display: grid;
    justify-items: center;
    align-content: start;
    gap: 2px;
    min-width: 0;
    text-align: center;
  }
  .n-pv {
    grid-area: 1 / 3;
  }
  .n-grid {
    grid-area: 3 / 1;
  }
  .n-house {
    grid-area: 3 / 3;
  }
  .n-battery {
    grid-area: 3 / 5;
  }
  .badge {
    display: inline-grid;
    place-items: center;
    width: var(--badge);
    height: var(--badge);
    margin-bottom: var(--sc-space-1);
    border-radius: 50%;
    color: var(--tone);
    background: var(--sc-surface-sunken);
    box-shadow: inset 0 0 0 2px var(--tone);
  }
  .name,
  .state,
  .soc {
    font-size: var(--sc-font-xs);
    color: var(--sc-text-muted);
    overflow-wrap: anywhere;
  }
  .name {
    font-weight: var(--sc-weight-strong);
  }
  .val {
    font-size: var(--sc-font-lg);
    font-weight: var(--sc-weight-strong);
    line-height: var(--sc-leading-tight);
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
  }
  .stale .val {
    color: var(--sc-text-muted);
  }
  .stale .state {
    color: var(--sc-warn);
    font-weight: var(--sc-weight-strong);
  }
  .bad .state {
    color: var(--sc-error);
    font-weight: var(--sc-weight-strong);
  }
  .bad .badge {
    color: var(--sc-error);
    box-shadow: inset 0 0 0 2px var(--sc-error);
  }
  .stale .badge {
    box-shadow: inset 0 0 0 2px var(--sc-warn);
  }
  .soc {
    display: grid;
    justify-items: center;
    gap: var(--sc-space-1);
    width: 100%;
    font-variant-numeric: tabular-nums;
    color: var(--sc-text);
  }
  .bar {
    display: block;
    width: min(100%, 5.5rem);
    height: 6px;
    border-radius: var(--sc-radius-pill);
    background: var(--sc-surface-sunken);
    overflow: hidden;
  }
  .fill {
    display: block;
    height: 100%;
    border-radius: inherit;
    background: var(--sc-energy-bat-in);
  }

  /* Lines: dashes for a flow, dots when idle; the dashes move with the power class. */
  .edge {
    --edge-c: var(--sc-outline-strong);
    position: relative;
    align-self: start;
    height: 3px;
    margin-top: calc(var(--badge) / 2 - 1.5px);
    background: radial-gradient(circle, var(--edge-c) 1.2px, transparent 1.6px) 0 50% / 8px 3px
      repeat-x;
  }
  .e-grid {
    grid-area: 3 / 2;
  }
  .e-battery {
    grid-area: 3 / 4;
  }
  .e-pv {
    grid-area: 2 / 3;
    justify-self: center;
    align-self: stretch;
    width: 3px;
    height: auto;
    margin: var(--sc-space-1) 0;
    background: radial-gradient(circle, var(--edge-c) 1.2px, transparent 1.6px) 50% 0 / 3px 8px
      repeat-y;
  }
  .edge.forward,
  .edge.reverse {
    --edge-c: var(--edge);
    background: linear-gradient(90deg, var(--edge-c) 0 7px, transparent 7px 12px) 0 0 / 12px 3px
      repeat-x;
  }
  .e-pv.forward {
    background: linear-gradient(180deg, var(--edge-c) 0 7px, transparent 7px 12px) 0 0 / 3px 12px
      repeat-y;
  }
  .edge.stale {
    opacity: 0.55;
  }
  .edge.moving {
    animation: dash-x var(--cycle, 2s) linear infinite;
  }
  .e-pv.moving {
    animation-name: dash-y;
  }
  .edge.reverse.moving {
    animation-direction: reverse;
  }
  @keyframes dash-x {
    to {
      background-position-x: 12px;
    }
  }
  @keyframes dash-y {
    to {
      background-position-y: 12px;
    }
  }
  /* Arrow head in the middle of a line that carries power. */
  .edge.forward::after,
  .edge.reverse::after {
    content: '';
    position: absolute;
    left: 50%;
    top: 50%;
    width: 9px;
    height: 11px;
    background: var(--edge-c);
    clip-path: polygon(0 0, 100% 50%, 0 100%);
    transform: translate(-50%, -50%) rotate(var(--rot, 0deg));
  }
  .edge.reverse::after {
    --rot: 180deg;
  }
  .e-pv.forward::after {
    --rot: 90deg;
  }

  /* --- surplus ------------------------------------------------------------------------- */
  .surplus {
    display: grid;
    gap: var(--sc-space-2);
    align-content: start;
    min-width: 0;
    padding-top: var(--sc-space-4);
    border-top: 1px solid var(--sc-outline);
  }
  .surplus > .chip-tag {
    justify-self: start;
  }
  .stats {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(8.5rem, 1fr));
    gap: var(--sc-space-2) var(--sc-space-4);
    margin: 0;
  }
  dt {
    font-size: var(--sc-font-xs);
    color: var(--sc-text-muted);
  }
  dd {
    margin: 0;
    font-size: var(--sc-font-md);
    font-weight: var(--sc-weight-strong);
    font-variant-numeric: tabular-nums;
  }
  dd.bad {
    color: var(--sc-error);
  }

  @media (min-width: 600px) {
    .flow {
      --badge: 48px;
    }
    .val {
      font-size: var(--sc-font-xl);
    }
    .name,
    .state,
    .soc {
      font-size: var(--sc-font-sm);
    }
  }
  @media (min-width: 1024px) {
    .hero {
      grid-template-columns: minmax(0, 3fr) minmax(15rem, 2fr);
      grid-template-rows: auto 1fr;
      column-gap: var(--sc-space-6);
    }
    .hero > .kicker {
      grid-column: 1 / -1;
    }
    .surplus {
      padding: 0 0 0 var(--sc-space-6);
      border-top: 0;
      border-inline-start: 1px solid var(--sc-outline);
      align-self: stretch;
      align-content: center;
    }
    .stats {
      grid-template-columns: 1fr 1fr;
      row-gap: var(--sc-space-4);
    }
    dd {
      font-size: var(--sc-font-lg);
    }
  }
</style>
