// SPDX-License-Identifier: Apache-2.0
import { describe, expect, it } from 'vitest';
import type { Health } from '../api/types';
import { LiveStore, linkStatus } from './live.svelte';

const health = (over: Partial<Health> = {}): Health => ({
  ok: true,
  ha_state: 'connected',
  ha_last_update_age_s: 1,
  db_ok: true,
  version: '0',
  ...over,
});

describe('linkStatus', () => {
  it('combines WebSocket and health', () => {
    expect(linkStatus('connecting', false, null)).toBe('connecting');
    expect(linkStatus('open', false, null)).toBe('connecting');
    expect(linkStatus('closed', true, health())).toBe('offline');
    expect(linkStatus('open', true, health())).toBe('live');
    expect(linkStatus('open', true, health({ ha_state: 'reconnecting' }))).toBe('ha_down');
    expect(linkStatus('open', true, health({ ok: false }))).toBe('ha_down');
  });
});

describe('LiveStore', () => {
  it('applies snapshot, flows, health and decisions messages', () => {
    const s = new LiveStore();
    s.setConnection('open');
    s.apply({ type: 'snapshot', flows: null, health: health(), decisions: [] });
    expect(s.haveSnapshot).toBe(true);
    expect(s.status).toBe('live');
    const flows = {
      grid_w: { value: 100, quality: 'ok' as const },
      pv_w: { value: 200, quality: 'ok' as const },
      battery_w: { value: 0, quality: 'ok' as const },
      house_w: { value: 300, quality: 'ok' as const },
      battery_soc_pct: { value: 50, quality: 'ok' as const },
      ts: null,
    };
    s.apply({ type: 'flows', flows });
    expect(s.flows?.house_w.value).toBe(300);
    expect(s.flowUpdates).toBe(2);
    s.apply({ type: 'health', health: health({ ha_state: 'disconnected' }) });
    expect(s.status).toBe('ha_down');
  });
  it('drops the live claim when the socket closes', () => {
    const s = new LiveStore();
    s.setConnection('open');
    s.apply({ type: 'snapshot', flows: null, health: health(), decisions: [] });
    s.setConnection('closed');
    expect(s.status).toBe('offline');
    s.setConnection('open');
    expect(s.status).toBe('connecting');
  });
});
