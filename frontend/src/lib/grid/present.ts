// SPDX-License-Identifier: Apache-2.0
// Badges and event rows of the grid chain (island, §14a, monthly peak; PR 6-M).
import type { GridEvent, GridStatus } from '../api/types';

export type GridBadge =
  | { kind: 'island' }
  | { kind: 'island_unknown' }
  | { kind: 'para14a' }
  | { kind: 'para14a_unknown' }
  | { kind: 'peak'; peakW: number; limitW: number | null; near: boolean };

/** Share of the peak limit from which the badge turns into a warning. */
export const PEAK_NEAR = 0.9;

export function gridBadges(status: GridStatus | null): GridBadge[] {
  if (!status) return [];
  const out: GridBadge[] = [];
  if (status.state === 'island') out.push({ kind: 'island' });
  else if (status.state === 'unknown') out.push({ kind: 'island_unknown' });
  if (status.para14a === 'active') out.push({ kind: 'para14a' });
  else if (status.para14a === 'unknown') out.push({ kind: 'para14a_unknown' });
  if (status.peak_limit_w != null && status.month_peak_w != null) {
    out.push({
      kind: 'peak',
      peakW: status.month_peak_w,
      limitW: status.peak_limit_w,
      near: status.peak_limit_w > 0 && status.month_peak_w >= status.peak_limit_w * PEAK_NEAR,
    });
  }
  return out;
}

/** Event kinds the backend stores (app/grid.py) with their catalogue key. */
export const GRID_EVENT_KEYS = {
  island_start: 'grid.event.island_start',
  island_end: 'grid.event.island_end',
  para14a_start: 'grid.event.para14a_start',
  para14a_end: 'grid.event.para14a_end',
  para14a_unknown: 'grid.event.para14a_unknown',
  import_limit_exceeded: 'grid.event.import_limit_exceeded',
  peak_new: 'grid.event.peak_new',
} as const;

export type GridEventKey = (typeof GRID_EVENT_KEYS)[keyof typeof GRID_EVENT_KEYS];

export function eventKey(event: Pick<GridEvent, 'kind'>): GridEventKey | null {
  return (GRID_EVENT_KEYS as Record<string, GridEventKey>)[event.kind] ?? null;
}
