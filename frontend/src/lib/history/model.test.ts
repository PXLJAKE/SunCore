// SPDX-License-Identifier: Apache-2.0
import { describe, expect, it } from 'vitest';
import type { HistorySummary } from '../api/types';
import {
  addDays,
  endOfRange,
  heatmapRange,
  hasData,
  historyHash,
  incompleteDays,
  neighbour,
  parseHistoryQuery,
  periodRange,
  planDayHash,
  trendRange,
} from './model';

const summary = (over: Partial<HistorySummary>): HistorySummary =>
  ({
    period: 'week',
    key: '2026-W25',
    start: '2026-06-15T00:00:00+02:00',
    end: '2026-06-22T00:00:00+02:00',
    days: 7,
    days_with_data: 7,
    days_complete: 7,
    in_progress: false,
    ...over,
  }) as HistorySummary;

describe('days', () => {
  it('adds days across month, year and leap-day borders', () => {
    expect(addDays('2026-06-30', 1)).toBe('2026-07-01');
    expect(addDays('2026-01-01', -1)).toBe('2025-12-31');
    expect(addDays('2024-02-28', 1)).toBe('2024-02-29');
  });
});

describe('url state', () => {
  it('reads period and date and falls back on anything invalid', () => {
    expect(parseHistoryQuery(new URLSearchParams('period=month&date=2026-03-05'))).toEqual({
      period: 'month',
      date: '2026-03-05',
    });
    expect(parseHistoryQuery(new URLSearchParams('period=decade&date=soon'))).toEqual({
      period: 'week',
      date: null,
    });
    expect(parseHistoryQuery(new URLSearchParams(''))).toEqual({ period: 'week', date: null });
  });

  it('round-trips through the hash', () => {
    const state = { period: 'year', date: '2025-12-31' } as const;
    const hash = historyHash(state);
    expect(hash).toBe('#/history?period=year&date=2025-12-31');
    expect(parseHistoryQuery(new URLSearchParams(hash.split('?')[1]))).toEqual(state);
    expect(historyHash({ period: 'day', date: null })).toBe('#/history?period=day');
  });

  it('links the plan chart to a day', () => {
    expect(planDayHash('2026-06-15')).toBe('#/plan?day=2026-06-15');
  });
});

describe('navigation', () => {
  it('steps to the day before the start and to the exclusive end', () => {
    const s = summary({});
    expect(neighbour(s, -1)).toBe('2026-06-14');
    expect(neighbour(s, 1)).toBe('2026-06-22');
  });
});

describe('ranges', () => {
  it('stops at today for a period in progress', () => {
    const s = summary({
      period: 'month',
      start: '2026-06-01T00:00:00+02:00',
      end: '2026-07-01T00:00:00+02:00',
      days: 21,
    });
    expect(periodRange(s)).toEqual({ from: '2026-06-01', to: '2026-06-21' });
    expect(trendRange(s)).toEqual({ from: '2026-06-01', to: '2026-06-21' });
  });

  it('shows 14 days of trend for a single day and 28 days of heatmap for a short period', () => {
    const day = summary({
      period: 'day',
      start: '2026-06-21T00:00:00+02:00',
      end: '2026-06-22T00:00:00+02:00',
      days: 1,
    });
    expect(trendRange(day)).toEqual({ from: '2026-06-08', to: '2026-06-21' });
    const heat = heatmapRange(day);
    expect(heat).toEqual({ from: '2026-05-25', to: '2026-06-21' });
    expect(endOfRange(heat)).toBe('2026-06-22T00:00:00');
  });

  it('keeps a long period as it is for the heatmap', () => {
    const year = summary({
      period: 'year',
      start: '2026-01-01T00:00:00+01:00',
      end: '2027-01-01T00:00:00+01:00',
      days: 172,
    });
    expect(heatmapRange(year)).toEqual({ from: '2026-01-01', to: '2026-06-21' });
  });
});

describe('completeness', () => {
  it('counts days that are missing or too sparse', () => {
    expect(incompleteDays(summary({}))).toBe(0);
    expect(incompleteDays(summary({ days_with_data: 6, days_complete: 5 }))).toBe(2);
  });

  it('knows the collecting state', () => {
    expect(hasData(null)).toBe(false);
    expect(hasData(summary({ days_with_data: 0 }))).toBe(false);
    expect(hasData(summary({}))).toBe(true);
  });
});
