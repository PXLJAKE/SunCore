// SPDX-License-Identifier: Apache-2.0
// Weekly time windows (grid-charge schedule, HT/NT periods, time-variable grid fees) <-> form rows.
// A window is a set of weekdays plus a local start and end; `end` before `start` crosses midnight
// and `start == end` is the whole day (the server's TouWindow semantics).
import type { GridFeeWindow, TouPeriod, TouWindow, WeeklySchedule } from '../api/types';
import { TIME_PATTERN } from './goalsForm';

export const ALL_DAYS: readonly number[] = [0, 1, 2, 3, 4, 5, 6];

export interface WindowRow {
  /** 0 = Monday ... 6 = Sunday. */
  days: number[];
  start: string;
  end: string;
}

export interface PeriodRow {
  window: WindowRow;
  price: number | null;
  /** Months 1-12 the price applies in; empty = all year. */
  months: number[];
}

export interface FeeRow {
  window: WindowRow;
  fee: number | null;
  months: number[];
}

export const emptyWindow = (): WindowRow => ({ days: [...ALL_DAYS], start: '22:00', end: '06:00' });

export const windowFromApi = (w: TouWindow): WindowRow => ({
  days: [...(w.days ?? ALL_DAYS)].sort((a, b) => a - b),
  start: w.start,
  end: w.end,
});

export const windowToApi = (w: WindowRow): TouWindow => ({
  days: [...w.days].sort((a, b) => a - b),
  start: w.start,
  end: w.end,
});

/** A window the server would accept: at least one day and two valid times. */
export const isWindowValid = (w: WindowRow): boolean =>
  w.days.length > 0 && TIME_PATTERN.test(w.start) && TIME_PATTERN.test(w.end);

export function toggleDay(w: WindowRow, day: number, on: boolean): void {
  const has = w.days.includes(day);
  if (on && !has) w.days = [...w.days, day].sort((a, b) => a - b);
  if (!on && has) w.days = w.days.filter((d) => d !== day);
}

export const toggleMonth = (months: number[], month: number, on: boolean): number[] => {
  const set = new Set(months);
  if (on) set.add(month);
  else set.delete(month);
  return [...set].sort((a, b) => a - b);
};

export const scheduleFromApi = (s: WeeklySchedule | null | undefined): WindowRow[] =>
  (s?.windows ?? []).map(windowFromApi);

/** `null` while no window is defined (a schedule needs at least one). */
export const scheduleToApi = (rows: readonly WindowRow[]): WeeklySchedule | null =>
  rows.length === 0 ? null : { windows: rows.map(windowToApi) };

function requirePrice(value: number | null): number {
  if (value == null) throw new Error('a time window needs its price');
  return value;
}

export const periodsFromApi = (periods: readonly TouPeriod[] | undefined): PeriodRow[] =>
  (periods ?? []).map((p) => ({
    window: windowFromApi(p.window),
    price: p.price,
    months: [...(p.months ?? [])].sort((a, b) => a - b),
  }));

/** Every period needs its price: a missing one is never stored as 0 (throws, see `isTariffComplete`). */
export const periodsToApi = (rows: readonly PeriodRow[]): TouPeriod[] =>
  rows.map((r) => ({
    window: windowToApi(r.window),
    price: requirePrice(r.price),
    ...(r.months.length === 0 ? {} : { months: [...r.months] }),
  }));

export const feesFromApi = (windows: readonly GridFeeWindow[] | undefined): FeeRow[] =>
  (windows ?? []).map((f) => ({
    window: windowFromApi(f.window),
    fee: f.fee,
    months: [...(f.months ?? [])].sort((a, b) => a - b),
  }));

export const feesToApi = (rows: readonly FeeRow[]): GridFeeWindow[] =>
  rows.map((r) => ({
    window: windowToApi(r.window),
    fee: requirePrice(r.fee),
    ...(r.months.length === 0 ? {} : { months: [...r.months] }),
  }));
