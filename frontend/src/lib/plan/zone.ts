// SPDX-License-Identifier: Apache-2.0
// Wall-clock time in the site's zone as an instant and back. The browser's own zone is never
// used: a day of the plan chart is the site's day (23 or 25 hours on a DST change).

const formats = new Map<string, Intl.DateTimeFormat>();

function formatter(zone: string): Intl.DateTimeFormat {
  let f = formats.get(zone);
  if (!f) {
    f = new Intl.DateTimeFormat('en-CA', {
      timeZone: zone,
      hourCycle: 'h23',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    });
    formats.set(zone, f);
  }
  return f;
}

/** Offset of `zone` from UTC at the instant `ms` (positive east of Greenwich). */
export function zoneOffsetMs(ms: number, zone: string): number {
  const p: Record<string, number> = {};
  for (const part of formatter(zone).formatToParts(new Date(ms))) {
    if (part.type !== 'literal') p[part.type] = Number(part.value);
  }
  const wall = Date.UTC(p.year!, p.month! - 1, p.day!, p.hour!, p.minute!, p.second!);
  return wall - Math.floor(ms / 1000) * 1000;
}

/** The instant at which a wall clock in `zone` shows `day` (`YYYY-MM-DD`) and `time` (`HH:MM[:SS]`). */
export function zonedMs(day: string, time: string, zone: string): number {
  const [y, m, d] = day.split('-').map(Number) as [number, number, number];
  const [h = 0, mi = 0, s = 0] = time.split(':').map(Number);
  const guess = Date.UTC(y, m - 1, d, h, mi, s);
  const first = guess - zoneOffsetMs(guess, zone);
  // Across a DST change the offset at the first guess can be the other one.
  return guess - zoneOffsetMs(first, zone);
}

/** `YYYY-MM-DD` of an instant on the wall clock of `zone`. */
export function dayInZone(ms: number, zone: string): string {
  return formatter(zone).format(new Date(ms)).slice(0, 10);
}

/** `YYYY-MM-DDTHH:MM` of an instant on the wall clock of `zone` (the `datetime-local` format). */
export function localInputValue(ms: number, zone: string): string {
  const s = formatter(zone).format(new Date(ms)); // `2026-06-21, 09:40:00`
  return `${s.slice(0, 10)}T${s.slice(12, 17)}`;
}

/** Instant of a `datetime-local` value read on the wall clock of `zone`; `null` if invalid. */
export function parseLocalInput(value: string, zone: string): number | null {
  const m = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}(?::\d{2})?)$/.exec(value);
  if (!m) return null;
  const ms = zonedMs(m[1]!, m[2]!, zone);
  return Number.isNaN(ms) ? null : ms;
}
