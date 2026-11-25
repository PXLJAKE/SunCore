// SPDX-License-Identifier: Apache-2.0
// The look-back side of the plan chart (WP7): the plan snapshot of a past time, the comparison of
// two snapshots and the measured series. Kept out of `client.ts` so the first-paint bundle does
// not carry it: only the lazily loaded chart panel imports this module.
import { API_PREFIX, apiUrl, query, request } from './client';
import type { HistorySeries, PlanAt, PlanCompare } from './types';

/** Query string with a repeated `key` parameter (the series endpoint takes up to eight). */
function seriesQuery(keys: readonly string[], p: Record<string, string | undefined>): string {
  const search = new URLSearchParams();
  for (const k of keys) search.append('key', k);
  for (const [k, v] of Object.entries(p)) if (v !== undefined) search.set(k, v);
  return `?${search.toString()}`;
}

export const planHistoryApi = {
  /** The snapshot valid at `ts` (404 `api.error.snapshot_not_found` when pruned). */
  at: (ts: string) => request<PlanAt>(`${API_PREFIX}plan/at${query({ ts })}`),
  /** `a` and `b` are a snapshot id or a time. */
  compare: (a: string, b: string) =>
    request<PlanCompare>(`${API_PREFIX}plan/compare${query({ a, b })}`),
  series: (keys: readonly string[], from: string, to: string, resolution: '15m' = '15m') =>
    request<HistorySeries>(
      `${API_PREFIX}history/series${seriesQuery(keys, { from, to, resolution })}`,
    ),
  /** Download links of the same queries as CSV (plain anchors). */
  atCsv: (ts: string): string =>
    apiUrl(`${API_PREFIX}plan/at${query({ ts, format: 'csv' })}`).toString(),
  compareCsv: (a: string, b: string): string =>
    apiUrl(`${API_PREFIX}plan/compare${query({ a, b, format: 'csv' })}`).toString(),
  seriesCsv: (keys: readonly string[], from: string, to: string): string =>
    apiUrl(
      `${API_PREFIX}history/series${seriesQuery(keys, { from, to, resolution: '15m', format: 'csv' })}`,
    ).toString(),
};
