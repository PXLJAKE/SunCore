// SPDX-License-Identifier: Apache-2.0
// The what-if view's REST side (WP6 job API). Kept out of `client.ts` so the first-paint bundle
// does not carry it: only the lazily loaded what-if view imports this module.
import { API_PREFIX, request } from './client';
import type { BacktestRequest, BacktestRun } from './types';

const JSON_HEADERS = { 'Content-Type': 'application/json' };

export const backtestApi = {
  /** The kept runs (the last five), newest first; progress but no results. */
  list: () => request<BacktestRun[]>(`${API_PREFIX}backtests`),
  /** One run; the result is there once it is `done`. */
  get: (id: number) => request<BacktestRun>(`${API_PREFIX}backtests/${id}`),
  /** Start a run (202 at once; poll `get`). 409 busy, 422 window/overlay, 503 no history. */
  start: (body: BacktestRequest) =>
    request<BacktestRun>(`${API_PREFIX}backtests`, {
      method: 'POST',
      headers: JSON_HEADERS,
      body: JSON.stringify(body),
    }),
  /** Cancels a running run (it stays as `cancelled`), deletes a finished one. */
  remove: (id: number) =>
    request<{ outcome: 'cancelled' | 'deleted' }>(`${API_PREFIX}backtests/${id}`, {
      method: 'DELETE',
    }),
};
