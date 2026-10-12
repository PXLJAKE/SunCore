<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import type { Flows, FlowValue } from '../api/types';
  import {
    activityOf,
    clampPercent,
    cycleSeconds,
    directionOf,
    edgeAngle,
    isUsable,
    pvDirection,
    speedLevel,
    type Activity,
    type Direction,
    type NodeId,
  } from '../flow';
  import { t, tPercent, tPower, type MessageKey } from '../i18n/index.svelte';

  interface Props {
    flows: Flows | null;
    hasBattery: boolean;
  }
  let { flows, hasBattery }: Props = $props();

  const NODE_W = 108;
  const NODE_H = 76;
  const NODE_POS: Record<NodeId, { cx: number; cy: number }> = {
    pv: { cx: 230, cy: 46 },
    grid: { cx: 62, cy: 170 },
    house: { cx: 230, cy: 170 },
    battery: { cx: 398, cy: 170 },
  };
  const EDGES = {
    pv: { x1: 230, y1: 84, x2: 230, y2: 132 },
    grid: { x1: 116, y1: 170, x2: 176, y2: 170 },
    battery: { x1: 284, y1: 170, x2: 344, y2: 170 },
  } as const;
  const DASH = '–';

  const LABEL: Record<NodeId, MessageKey> = {
    pv: 'node.pv',
    grid: 'node.grid',
    battery: 'node.battery',
    house: 'node.house',
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

  const unknown: FlowValue = { value: null, quality: 'unavailable' };
  function flowOf(id: NodeId): FlowValue {
    if (!flows) return unknown;
    return { pv: flows.pv_w, grid: flows.grid_w, battery: flows.battery_w, house: flows.house_w }[
      id
    ];
  }

  interface NodeView {
    id: NodeId;
    flow: FlowValue;
    value: string;
    status: string;
    marked: boolean;
    quality: FlowValue['quality'];
  }

  function nodeView(id: NodeId, f: FlowValue): NodeView {
    const usable = isUsable(f);
    return {
      id,
      flow: f,
      value: usable ? tPower(Math.abs(f.value)) : DASH,
      status: f.quality === 'ok' ? t(ACTIVITY[activityOf(id, f)]) : t(QUALITY[f.quality]),
      marked: f.quality !== 'ok',
      quality: f.quality,
    };
  }

  const nodes = $derived.by(() => {
    const ids: NodeId[] = hasBattery ? ['pv', 'grid', 'house', 'battery'] : ['pv', 'grid', 'house'];
    return ids.map((id) => nodeView(id, flowOf(id)));
  });

  const soc = $derived(flows?.battery_soc_pct ?? unknown);
  const socPct = $derived(isUsable(soc) ? clampPercent(soc.value) : null);

  interface EdgeView {
    id: 'pv' | 'grid' | 'battery';
    dir: Direction;
    level: number;
    kind: string;
    mx: number;
    my: number;
    angle: number;
    stale: boolean;
    x1: number;
    y1: number;
    x2: number;
    y2: number;
  }

  function edgeView(id: 'pv' | 'grid' | 'battery'): EdgeView {
    const f = flowOf(id);
    const dir = id === 'pv' ? pvDirection(f) : directionOf(f);
    const e = EDGES[id];
    const kind =
      id === 'pv'
        ? 'pv'
        : id === 'grid'
          ? dir === 'reverse'
            ? 'grid-export'
            : 'grid-import'
          : dir === 'reverse'
            ? 'battery-out'
            : 'battery-in';
    return {
      id,
      dir,
      level: speedLevel(f),
      kind,
      mx: (e.x1 + e.x2) / 2,
      my: (e.y1 + e.y2) / 2,
      angle: edgeAngle(e.x1, e.y1, e.x2, e.y2, dir),
      stale: f.quality === 'stale',
      ...e,
    };
  }

  const edges = $derived(
    (hasBattery ? (['pv', 'grid', 'battery'] as const) : (['pv', 'grid'] as const)).map(edgeView),
  );
</script>

<figure class="flow" aria-label={t('now.diagram.label')}>
  <svg viewBox="0 0 460 230" aria-hidden="true" focusable="false">
    {#each edges as e (e.id)}
      <g class="edge {e.kind}" class:idle={e.dir === 'idle'} class:reverse={e.dir === 'reverse'}>
        <line
          class="edge-line"
          class:moving={e.level > 0}
          class:stale={e.stale}
          x1={e.x1}
          y1={e.y1}
          x2={e.x2}
          y2={e.y2}
          style:--cycle="{cycleSeconds(e.level)}s"
        />
        {#if e.dir !== 'idle'}
          <polygon
            class="chevron"
            points="-6,-6 6,0 -6,6"
            transform="translate({e.mx} {e.my}) rotate({e.angle})"
          />
        {/if}
      </g>
    {/each}

    {#each nodes as n (n.id)}
      {@const pos = NODE_POS[n.id]}
      <g
        class="node {n.id}"
        class:stale={n.quality === 'stale'}
        class:bad={n.quality === 'unavailable' || n.quality === 'invalid'}
        data-testid="node-{n.id}"
      >
        <rect
          class="box"
          x={pos.cx - NODE_W / 2}
          y={pos.cy - NODE_H / 2}
          width={NODE_W}
          height={NODE_H}
          rx="12"
        />
        <text class="label" x={pos.cx - NODE_W / 2 + 10} y={pos.cy - 20}>{t(LABEL[n.id])}</text>
        {#if n.id === 'battery'}
          <text class="soc" x={pos.cx + NODE_W / 2 - 10} y={pos.cy - 20} text-anchor="end">
            {socPct === null ? DASH : tPercent(socPct)}
          </text>
        {/if}
        <text class="value" x={pos.cx - NODE_W / 2 + 10} y={pos.cy + 4}>{n.value}</text>
        <text class="status" x={pos.cx - NODE_W / 2 + 10} y={pos.cy + 22}>{n.status}</text>
        {#if n.id === 'battery'}
          <rect
            class="soc-track"
            x={pos.cx - NODE_W / 2 + 10}
            y={pos.cy + 29}
            width={NODE_W - 20}
            height="5"
            rx="2.5"
          />
          {#if socPct !== null}
            <rect
              class="soc-fill"
              x={pos.cx - NODE_W / 2 + 10}
              y={pos.cy + 29}
              width={((NODE_W - 20) * socPct) / 100}
              height="5"
              rx="2.5"
            />
          {/if}
        {/if}
      </g>
    {/each}
  </svg>

  <figcaption class="sr-only">{t('now.diagram.caption')}</figcaption>
</figure>

<style>
  .flow {
    margin: 0;
  }
  svg {
    display: block;
    width: 100%;
    max-width: 760px;
    margin: 0 auto;
    height: auto;
  }
  .box {
    fill: var(--card-background-color);
    stroke: var(--divider-color);
    stroke-width: 1.5;
  }
  .node.stale .box {
    stroke: var(--warning-color);
    stroke-dasharray: 5 4;
  }
  .node.bad .box {
    stroke: var(--error-color);
    stroke-dasharray: 2 4;
    stroke-width: 2;
  }
  .node.stale .value {
    opacity: 0.7;
  }
  .node.stale .status {
    fill: var(--warning-color);
  }
  .node.bad .status {
    fill: var(--error-color);
  }
  text {
    fill: var(--primary-text-color);
    font-family: inherit;
  }
  .label,
  .soc,
  .status {
    font-size: 12.5px;
    fill: var(--secondary-text-color);
  }
  .soc {
    fill: var(--primary-text-color);
    font-weight: 600;
  }
  .value {
    font-size: 21px;
    font-weight: 600;
  }
  .soc-track {
    fill: var(--secondary-background-color);
  }
  .soc-fill {
    fill: var(--energy-battery-in-color);
  }

  .edge-line {
    fill: none;
    stroke: var(--divider-color);
    stroke-width: 3;
    stroke-linecap: round;
    stroke-dasharray: 2 7;
  }
  .edge:not(.idle) .edge-line {
    stroke-dasharray: 7 7;
  }
  .edge-line.stale {
    stroke-dasharray: 2 6;
    opacity: 0.55;
  }
  .edge-line.moving {
    animation: flow var(--cycle, 2s) linear infinite;
  }
  .edge.reverse .edge-line.moving {
    animation-direction: reverse;
  }
  @keyframes flow {
    to {
      stroke-dashoffset: -14;
    }
  }
  .chevron {
    fill: var(--edge-color, var(--secondary-text-color));
  }
  .edge.pv {
    --edge-color: var(--energy-solar-color);
  }
  .edge.grid-import {
    --edge-color: var(--energy-grid-consumption-color);
  }
  .edge.grid-export {
    --edge-color: var(--energy-grid-return-color);
  }
  .edge.battery-in {
    --edge-color: var(--energy-battery-in-color);
  }
  .edge.battery-out {
    --edge-color: var(--energy-battery-out-color);
  }
  .edge:not(.idle) .edge-line {
    stroke: var(--edge-color);
  }
</style>
