// SPDX-License-Identifier: Apache-2.0
// Notification settings helpers (PR 6-M).
import type { NotificationsConfig } from '../api/types';

type Target = NotificationsConfig['targets'][number];

export const CATEGORIES = [
  'error',
  'safety',
  'proposal',
  'program',
  'ev_target',
  'cheap_window',
  'daily_summary',
] as const satisfies readonly (keyof NotificationsConfig['categories'])[];

export type Category = (typeof CATEGORIES)[number];

/** At most this many push targets (core/config.py `NotificationsConfig.targets`). */
export const MAX_TARGETS = 8;

export const targetKey = (t: Pick<Target, 'kind' | 'id'>): string => `${t.kind}:${t.id}`;

/** The list with the target added once (a duplicate or a ninth target is ignored). */
export function addTarget(targets: readonly Target[], kind: Target['kind'], id: string): Target[] {
  const wanted = id.trim();
  if (wanted === '' || targets.length >= MAX_TARGETS) return [...targets];
  if (targets.some((x) => x.kind === kind && x.id === wanted)) return [...targets];
  return [...targets, { kind, id: wanted }];
}

export const removeTarget = (targets: readonly Target[], target: Target): Target[] =>
  targets.filter((x) => targetKey(x) !== targetKey(target));
