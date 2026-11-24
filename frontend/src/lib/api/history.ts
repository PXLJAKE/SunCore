// SPDX-License-Identifier: Apache-2.0
// The read side of the history view (WP4). Kept out of `client.ts` so the first-paint bundle does
// not carry it: only the lazily loaded history view imports this module.
import { API_PREFIX, apiUrl, query, request } from './client';
import type {
  HistoryCycles,
  HistoryDevices,
  HistoryHeatmap,
  HistorySankey,
  HistorySkill,
  HistorySummary,
} from './types';

/** Endpoints of the history API; every one also answers `format=csv`. */
export type HistoryEndpoint =
  'summary' | 'sankey' | 'heatmap' | 'cycles' | 'forecast-skill' | 'devices';

/** Query of a history endpoint: `undefined` values are left out. */
export type HistoryParams = Record<string, string | undefined>;

export const historyApi = {
  summary: (p: HistoryParams) => request<HistorySummary>(historyPath('summary', p)),
  sankey: (p: HistoryParams) => request<HistorySankey>(historyPath('sankey', p)),
  heatmap: (p: HistoryParams) => request<HistoryHeatmap>(historyPath('heatmap', p)),
  cycles: (p: HistoryParams) => request<HistoryCycles>(historyPath('cycles', p)),
  skill: (p: HistoryParams) => request<HistorySkill>(historyPath('forecast-skill', p)),
  devices: (p: HistoryParams) => request<HistoryDevices>(historyPath('devices', p)),
  /** Download link of the same query as CSV (a plain anchor, resolved against the document). */
  csvUrl: (endpoint: HistoryEndpoint, p: HistoryParams): string =>
    apiUrl(historyPath(endpoint, { ...p, format: 'csv' })).toString(),
};

function historyPath(endpoint: HistoryEndpoint, p: HistoryParams): string {
  return `${API_PREFIX}history/${endpoint}${query(p)}`;
}
