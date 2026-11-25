// SPDX-License-Identifier: Apache-2.0
// Which intervals the chart draws: the live plan, completed on its past side by the snapshot that
// was valid at the start of the window (the live plan only starts at its own computation), or a
// snapshot alone. Pure.
import type { PlanInterval, PlanView } from '../api/types';
import type { PlanTarget, TimeWindow } from './target';

/** Start of the first interval of the live plan (`Infinity` without a plan). */
export function liveStartMs(view: Pick<PlanView, 'plan'> | null): number {
  const first = view?.plan.intervals[0];
  return first ? Date.parse(first.start) : Number.POSITIVE_INFINITY;
}

/** Whether the window needs a snapshot besides (or instead of) the live plan. */
export function needsSnapshot(
  target: PlanTarget,
  win: TimeWindow,
  view: Pick<PlanView, 'plan'> | null,
): boolean {
  return target.kind === 'at' || win.fromMs < liveStartMs(view);
}

/** The time a snapshot is asked for: the decision's time, or the start of the window. */
export const snapshotTime = (target: PlanTarget, win: TimeWindow): number =>
  target.kind === 'at' ? target.atMs : win.fromMs;

/** The time that identifies the plan on screen for `/plan/compare` (`a`). */
export function referenceTime(target: PlanTarget, win: TimeWindow, nowMs: number): number {
  if (target.kind === 'at') return target.atMs;
  return Math.min(nowMs, Math.max(win.fromMs, win.toMs - 1));
}

/** Snapshot intervals first (only what comes before the live plan), then the live plan. */
export function stitch(
  earlier: readonly PlanInterval[],
  live: readonly PlanInterval[],
): PlanInterval[] {
  const first = live[0] ? Date.parse(live[0].start) : Number.POSITIVE_INFINITY;
  return [...earlier.filter((i) => Date.parse(i.end) <= first), ...live];
}

/** The intervals that overlap the window. */
export function inWindow(intervals: readonly PlanInterval[], win: TimeWindow): PlanInterval[] {
  return intervals.filter((i) => Date.parse(i.end) > win.fromMs && Date.parse(i.start) < win.toMs);
}

/** What the chart draws for a target. */
export function chartIntervals(
  target: PlanTarget,
  win: TimeWindow,
  view: Pick<PlanView, 'plan'> | null,
  snapshot: readonly PlanInterval[] | null,
): PlanInterval[] {
  const live = view?.plan.intervals ?? [];
  const all = target.kind === 'at' ? (snapshot ?? []) : stitch(snapshot ?? [], live);
  return inWindow(all, win);
}
