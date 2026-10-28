// SPDX-License-Identifier: Apache-2.0
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError, planApi } from '../api/client';
import type { PlanStatus, PlanView } from '../api/types';
import { live } from './live.svelte';
import { PlanStore, REPLAN_COOLDOWN_MS } from './plan.svelte';

const view = {
  generated_at: '2026-06-21T09:30:00+02:00',
  plan: { intervals: [] },
} as unknown as PlanView;
const status = { running: false } as unknown as PlanStatus;

let store: PlanStore;
beforeEach(() => {
  store = new PlanStore();
  vi.useFakeTimers();
  vi.spyOn(planApi, 'plan').mockResolvedValue(view);
  vi.spyOn(planApi, 'status').mockResolvedValue(status);
});
afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe('PlanStore', () => {
  it('loads plan and status and takes "now" from the server', async () => {
    await store.refresh();
    expect(store.view).toBe(view);
    expect(store.loaded).toBe(true);
    expect(store.nowMs).toBe(Date.parse('2026-06-21T09:30:00+02:00'));
  });

  it('reports a missing planner (409) as unavailable', async () => {
    vi.spyOn(planApi, 'plan').mockRejectedValue(
      new ApiError(409, 'api.error.planning_unavailable'),
    );
    await store.refresh();
    expect(store.unavailable).toBe(true);
    expect(store.loaded).toBe(true);
  });

  it('debounces replan: one request while busy, feedback, then idle again', async () => {
    let release: (v: { accepted: boolean; earliest_start_at: string }) => void = () => {};
    const replan = vi
      .spyOn(planApi, 'replan')
      .mockImplementation(() => new Promise((resolve) => (release = resolve)));
    const first = store.replan();
    void store.replan();
    expect(store.replanState).toBe('busy');
    expect(replan).toHaveBeenCalledTimes(1);
    release({ accepted: true, earliest_start_at: '2026-06-21T09:31:00+02:00' });
    await first;
    expect(store.replanState).toBe('accepted');
    vi.advanceTimersByTime(REPLAN_COOLDOWN_MS + 1);
    expect(store.replanState).toBe('idle');
  });

  it('says when the server refused to start a run now', async () => {
    vi.spyOn(planApi, 'replan').mockResolvedValue({
      accepted: false,
      earliest_start_at: '2026-06-21T09:31:00+02:00',
    });
    await store.replan();
    expect(store.replanState).toBe('limited');
    expect(store.replanAck?.earliest_start_at).toBe('2026-06-21T09:31:00+02:00');
  });

  it('keeps the error of a failed request', async () => {
    vi.spyOn(planApi, 'replan').mockRejectedValue(new ApiError(0, 'api.error.network'));
    await store.replan();
    expect(store.replanState).toBe('error');
    expect(store.replanError).toBe('api.error.network');
  });

  it('refetches on a plan message of the feed, not on the first look', async () => {
    const spy = vi.spyOn(store, 'refresh').mockResolvedValue();
    live.planUpdates = 0;
    store.syncWithFeed();
    expect(spy).not.toHaveBeenCalled();
    live.planUpdates = 1;
    store.syncWithFeed();
    store.syncWithFeed();
    expect(spy).toHaveBeenCalledTimes(1);
  });

  it('knows when an Open-Meteo source is configured', () => {
    expect(store.usesOpenMeteo).toBe(false);
    store.forecastConfig = {
      sources: [{ template_id: 'open_meteo_solar_forecast.pv' }],
    } as never;
    expect(store.usesOpenMeteo).toBe(true);
  });
});
