// SPDX-License-Identifier: Apache-2.0
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError, controlApi } from '../api/client';
import type { Decision } from '../api/types';
import { DecisionLogStore } from './decisionLog.svelte';

afterEach(() => vi.restoreAllMocks());

const row = (id: string) =>
  ({ intent: { id }, ts: '2026-06-21T09:30:00Z', kind: 'shadow' }) as Decision;

describe('DecisionLogStore', () => {
  it('asks the server for the selected device and kinds', async () => {
    const spy = vi.spyOn(controlApi, 'decisions').mockResolvedValue([row('a')]);
    const log = new DecisionLogStore();
    log.deviceId = 'ac';
    log.toggleKind('shadow');
    log.toggleKind('failed');
    log.toggleKind('shadow');
    await log.load();
    expect(spy).toHaveBeenCalledWith({ limit: 200, deviceId: 'ac', kinds: ['failed'] });
    expect(log.items.map((d) => d.intent.id)).toEqual(['a']);
  });

  it('sends no device filter for "all devices"', async () => {
    const spy = vi.spyOn(controlApi, 'decisions').mockResolvedValue([]);
    await new DecisionLogStore().load();
    expect(spy).toHaveBeenCalledWith({ limit: 200, deviceId: undefined, kinds: [] });
  });

  it('drops the answer of an older filter that arrives late', async () => {
    const resolvers: ((rows: Decision[]) => void)[] = [];
    vi.spyOn(controlApi, 'decisions').mockImplementation(
      () => new Promise((resolve) => resolvers.push(resolve)),
    );
    const log = new DecisionLogStore();
    const slow = log.load();
    const fast = log.load();
    resolvers[1]?.([row('new')]);
    await fast;
    resolvers[0]?.([row('old')]);
    await slow;
    expect(log.items.map((d) => d.intent.id)).toEqual(['new']);
    expect(log.loading).toBe(false);
  });

  it('keeps the list and reports the error key when the request fails', async () => {
    vi.spyOn(controlApi, 'decisions').mockResolvedValueOnce([row('a')]);
    const log = new DecisionLogStore();
    await log.load();
    vi.spyOn(controlApi, 'decisions').mockRejectedValue(new ApiError(0, 'api.error.network'));
    await log.load();
    expect(log.error).toBe('api.error.network');
    expect(log.items).toHaveLength(1);
  });
});
