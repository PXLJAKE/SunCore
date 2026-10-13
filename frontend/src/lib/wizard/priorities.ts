// SPDX-License-Identifier: Apache-2.0
// Priority list: reducer for moves (drag & drop and buttons share it) and the pointer geometry.
import type { DeviceConfig } from '../api/types';

/** Move the item at `from` to position `to` (both clamped); returns a new array. */
export function moveItem<T>(list: readonly T[], from: number, to: number): T[] {
  const out = [...list];
  if (out.length === 0 || from < 0 || from >= out.length) return out;
  const target = Math.min(Math.max(to, 0), out.length - 1);
  const [item] = out.splice(from, 1);
  out.splice(target, 0, item!);
  return out;
}

/** Where a dragged row would land given the vertical centres of all rows (in list order). */
export function dropIndex(centers: readonly number[], pointerY: number): number {
  let index = 0;
  for (const c of centers) {
    if (pointerY > c) index += 1;
  }
  return Math.min(index, Math.max(centers.length - 1, 0));
}

/**
 * Drop position while dragging row `from`: the pointer compares against the centres of the
 * other rows, so the dragged row's own slot does not shift the result.
 */
export function dropIndexWhileDragging(
  centers: readonly number[],
  from: number,
  pointerY: number,
): number {
  let index = 0;
  centers.forEach((c, i) => {
    if (i !== from && pointerY > c) index += 1;
  });
  return Math.min(index, Math.max(centers.length - 1, 0));
}

/** Devices in priority order (lower first, ties by id) as the list shows them. */
export function orderedDevices(devices: readonly DeviceConfig[]): DeviceConfig[] {
  return [...devices].sort((a, b) => a.priority - b.priority || a.id.localeCompare(b.id));
}

/** Store the list order in `priority`: position 0 is served first. */
export function withPriorities(
  devices: readonly DeviceConfig[],
  order: readonly string[],
): DeviceConfig[] {
  const rank = new Map(order.map((id, i) => [id, i]));
  return devices.map((d) => ({ ...d, priority: rank.get(d.id) ?? d.priority }));
}

export type MoveDirection = 'up' | 'down' | 'first' | 'last';

export function targetFor(index: number, count: number, direction: MoveDirection): number {
  switch (direction) {
    case 'up':
      return Math.max(index - 1, 0);
    case 'down':
      return Math.min(index + 1, count - 1);
    case 'first':
      return 0;
    case 'last':
      return count - 1;
  }
}
