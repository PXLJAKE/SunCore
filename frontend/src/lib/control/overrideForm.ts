// SPDX-License-Identifier: Apache-2.0
// Device override form: boost for N minutes, pause until a time (or until resumed), resume.
import type { OverrideCommand } from '../api/types';

/** Bounds of `OverrideCommand.minutes` (core/controlstate.py). */
export const BOOST_MIN = 1;
export const BOOST_MAX = 1440;

export type OverrideError = 'boost_minutes' | 'pause_invalid' | 'pause_past';

export type OverrideResult =
  { ok: true; command: OverrideCommand } | { ok: false; error: OverrideError };

/** `boost` needs a whole number of minutes in 1..1440. */
export function boostCommand(minutes: number | null): OverrideResult {
  if (
    minutes === null ||
    !Number.isInteger(minutes) ||
    minutes < BOOST_MIN ||
    minutes > BOOST_MAX
  ) {
    return { ok: false, error: 'boost_minutes' };
  }
  return { ok: true, command: { action: 'boost', minutes } };
}

/**
 * `pause`: `until` is the value of a `<datetime-local>` input (`YYYY-MM-DDTHH:mm`, the user's
 * local time); empty means "until resumed". The command carries an absolute instant, so the
 * server (which needs a timezone-aware value) does not depend on the browser's zone.
 */
export function pauseCommand(until: string, now: Date = new Date()): OverrideResult {
  const text = until.trim();
  if (text === '') return { ok: true, command: { action: 'pause' } };
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?$/.test(text)) {
    return { ok: false, error: 'pause_invalid' };
  }
  const at = new Date(text); // no zone suffix: parsed as local time
  if (Number.isNaN(at.getTime())) return { ok: false, error: 'pause_invalid' };
  if (at.getTime() <= now.getTime()) return { ok: false, error: 'pause_past' };
  return { ok: true, command: { action: 'pause', until: at.toISOString() } };
}

export const resumeCommand = (): OverrideCommand => ({ action: 'resume' });

/** Value for `<input type="datetime-local" min>`: the current minute in local time. */
export function localDateTimeValue(date: Date): string {
  const pad = (n: number): string => String(n).padStart(2, '0');
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}` +
    `T${pad(date.getHours())}:${pad(date.getMinutes())}`
  );
}
