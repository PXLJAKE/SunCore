// SPDX-License-Identifier: Apache-2.0
// Flow maths for the "Now" view. Pure functions, no DOM, no framework.
//
// Sign convention of the API (core): grid + = import, battery + = charging, PV + = generation,
// house + = consumption. All values in W; SoC in percent.
import type { FlowValue, Quality } from './api/types';

/** Below this magnitude a flow counts as idle (noise, rounding of sensors). */
export const IDLE_THRESHOLD_W = 10;

export type Direction = 'idle' | 'forward' | 'reverse';

export type NodeId = 'pv' | 'grid' | 'battery' | 'house';

/** A value that can be shown as a number (stale values are shown, but marked). */
export function isUsable(
  flow: FlowValue | null | undefined,
): flow is FlowValue & { value: number } {
  return (
    !!flow &&
    flow.value !== null &&
    Number.isFinite(flow.value) &&
    (flow.quality === 'ok' || flow.quality === 'stale')
  );
}

/** Whether the flow is shown as live motion: usable and fresh. */
export function isAnimatable(
  flow: FlowValue | null | undefined,
): flow is FlowValue & { value: number } {
  return isUsable(flow) && flow.quality === 'ok';
}

/**
 * Direction of an edge. `forward` means along the drawn edge (source -> target):
 *  - pv edge:      PV -> house        forward when generating
 *  - grid edge:    grid -> house      forward when importing, reverse when exporting
 *  - battery edge: house -> battery   forward when charging, reverse when discharging
 *  - house edge:   not drawn; the house is the hub.
 */
export function directionOf(flow: FlowValue | null | undefined): Direction {
  if (!isUsable(flow)) return 'idle';
  if (Math.abs(flow.value) < IDLE_THRESHOLD_W) return 'idle';
  return flow.value > 0 ? 'forward' : 'reverse';
}

/** The PV edge only ever flows towards the house; tiny negative values (night offsets) are idle. */
export function pvDirection(flow: FlowValue | null | undefined): Direction {
  return directionOf(flow) === 'forward' ? 'forward' : 'idle';
}

/**
 * Animation speed level 0..5 (0 = no motion). Quantised on a log scale so the CSS animation
 * duration changes rarely (each change restarts the animation) – one level per power class.
 */
export const SPEED_LEVELS = 5;
const LEVEL_UPPER_BOUNDS_W = [100, 400, 1500, 4000] as const;

export function speedLevel(flow: FlowValue | null | undefined): number {
  if (!isAnimatable(flow)) return 0;
  const w = Math.abs(flow.value);
  if (w < IDLE_THRESHOLD_W) return 0;
  const idx = LEVEL_UPPER_BOUNDS_W.findIndex((bound) => w < bound);
  return idx === -1 ? SPEED_LEVELS : idx + 1;
}

/** Seconds per dash cycle for a speed level (higher level = faster). */
export function cycleSeconds(level: number): number {
  const clamped = Math.min(SPEED_LEVELS, Math.max(1, level));
  return [3.2, 2.2, 1.5, 1.0, 0.6][clamped - 1] ?? 3.2;
}

export type Activity =
  'idle' | 'importing' | 'exporting' | 'charging' | 'discharging' | 'producing' | 'consuming';

export function activityOf(node: NodeId, flow: FlowValue | null | undefined): Activity {
  const dir = node === 'pv' ? pvDirection(flow) : directionOf(flow);
  if (dir === 'idle') return 'idle';
  switch (node) {
    case 'grid':
      return dir === 'forward' ? 'importing' : 'exporting';
    case 'battery':
      return dir === 'forward' ? 'charging' : 'discharging';
    case 'pv':
      return 'producing';
    case 'house':
      return 'consuming';
  }
}

/** Quality ranking for "worst of" decisions (e.g. a node combining two signals). */
const RANK: Record<Quality, number> = { ok: 0, stale: 1, invalid: 2, unavailable: 3 };
export function worstQuality(...qualities: Quality[]): Quality {
  return qualities.reduce<Quality>((a, b) => (RANK[b] > RANK[a] ? b : a), 'ok');
}

export function clampPercent(pct: number): number {
  return Math.min(100, Math.max(0, pct));
}

/** Rotation (deg) of the direction chevron on an edge from (x1,y1) to (x2,y2). */
export function edgeAngle(x1: number, y1: number, x2: number, y2: number, dir: Direction): number {
  const base = (Math.atan2(y2 - y1, x2 - x1) * 180) / Math.PI;
  return dir === 'reverse' ? base + 180 : base;
}
