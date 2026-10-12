// SPDX-License-Identifier: Apache-2.0
import { describe, expect, it } from 'vitest';
import { apiUrl, liveUrl } from './client';
import type { LiveMessage } from './types';
import {
  BACKOFF_BASE_MS,
  BACKOFF_CAP_MS,
  LiveClient,
  backoffDelay,
  parseMessage,
  type ConnectionState,
  type SocketLike,
} from './ws';

class FakeSocket implements SocketLike {
  onopen: ((ev: unknown) => void) | null = null;
  onmessage: ((ev: { data: unknown }) => void) | null = null;
  onclose: ((ev: unknown) => void) | null = null;
  onerror: ((ev: unknown) => void) | null = null;
  closed = false;
  constructor(readonly url: string) {}
  close(): void {
    this.closed = true;
  }
}

function harness(random = () => 0.5) {
  const sockets: FakeSocket[] = [];
  const timers: { fn: () => void; ms: number; id: number }[] = [];
  const states: ConnectionState[] = [];
  const messages: LiveMessage[] = [];
  let nextId = 1;
  const client = new LiveClient({
    url: 'ws://x/api/v1/live',
    onMessage: (m) => messages.push(m),
    onState: (s) => states.push(s),
    createSocket: (u) => {
      const s = new FakeSocket(u);
      sockets.push(s);
      return s;
    },
    random,
    setTimer: (fn, ms) => {
      const id = nextId++;
      timers.push({ fn, ms, id });
      return id;
    },
    clearTimer: (h) => {
      const i = timers.findIndex((t) => t.id === h);
      if (i >= 0) timers.splice(i, 1);
    },
  });
  return { client, sockets, timers, states, messages };
}

const flowsFrame = JSON.stringify({ type: 'flows', flows: null });

describe('backoffDelay', () => {
  it('grows exponentially, capped, with jitter between 50 % and 100 %', () => {
    expect(backoffDelay(0, () => 0)).toBe(BACKOFF_BASE_MS / 2);
    expect(backoffDelay(0, () => 1)).toBe(BACKOFF_BASE_MS);
    expect(backoffDelay(1, () => 1)).toBe(2 * BACKOFF_BASE_MS);
    expect(backoffDelay(3, () => 1)).toBe(8 * BACKOFF_BASE_MS);
    expect(backoffDelay(50, () => 1)).toBe(BACKOFF_CAP_MS);
    expect(backoffDelay(50, () => 0)).toBe(BACKOFF_CAP_MS / 2);
  });
  it('is never below half the exponential value nor above the cap', () => {
    for (let a = 0; a < 20; a++) {
      for (const r of [0, 0.25, 0.5, 0.99]) {
        const d = backoffDelay(a, () => r);
        expect(d).toBeLessThanOrEqual(BACKOFF_CAP_MS);
        expect(d).toBeGreaterThanOrEqual(Math.min(BACKOFF_CAP_MS, BACKOFF_BASE_MS * 2 ** a) / 2);
      }
    }
  });
});

describe('parseMessage', () => {
  it('accepts known types and rejects junk', () => {
    expect(parseMessage(flowsFrame)?.type).toBe('flows');
    expect(parseMessage('{"type":"nope"}')).toBeNull();
    expect(parseMessage('not json')).toBeNull();
    expect(parseMessage('null')).toBeNull();
    expect(parseMessage(new ArrayBuffer(2))).toBeNull();
  });
});

describe('LiveClient', () => {
  it('connects, forwards messages and reports state', () => {
    const h = harness();
    h.client.start();
    expect(h.sockets).toHaveLength(1);
    expect(h.states).toEqual(['connecting']);
    h.sockets[0]!.onopen?.({});
    h.sockets[0]!.onmessage?.({ data: flowsFrame });
    h.sockets[0]!.onmessage?.({ data: 'garbage' });
    expect(h.states.at(-1)).toBe('open');
    expect(h.messages).toHaveLength(1);
  });

  it('reconnects with growing delays and resets after a message', () => {
    const h = harness(() => 1); // jitter off: delay = full exponential value
    h.client.start();
    h.sockets[0]!.onclose?.({});
    expect(h.timers.map((t) => t.ms)).toEqual([1000]);
    h.timers.shift()!.fn();
    h.sockets[1]!.onclose?.({});
    expect(h.timers[0]!.ms).toBe(2000);
    h.timers.shift()!.fn();
    h.sockets[2]!.onclose?.({});
    expect(h.timers[0]!.ms).toBe(4000);
    h.timers.shift()!.fn();
    // A successful open alone does not reset; a delivered message does.
    h.sockets[3]!.onopen?.({});
    h.sockets[3]!.onclose?.({});
    expect(h.timers[0]!.ms).toBe(8000);
    h.timers.shift()!.fn();
    h.sockets[4]!.onmessage?.({ data: flowsFrame });
    h.sockets[4]!.onclose?.({});
    expect(h.timers[0]!.ms).toBe(1000);
  });

  it('stop() cancels the pending reconnect and closes the socket', () => {
    const h = harness();
    h.client.start();
    h.sockets[0]!.onclose?.({});
    expect(h.timers).toHaveLength(1);
    h.client.stop();
    expect(h.timers).toHaveLength(0);
    expect(h.states.at(-1)).toBe('closed');

    const h2 = harness();
    h2.client.start();
    h2.client.stop();
    expect(h2.sockets[0]!.closed).toBe(true);
    h2.sockets[0]!.onclose?.({}); // late event after stop must not reconnect
    expect(h2.timers).toHaveLength(0);
  });

  it('retries when creating the socket throws', () => {
    const timers: number[] = [];
    const client = new LiveClient({
      url: 'ws://x',
      onMessage: () => {},
      onState: () => {},
      createSocket: () => {
        throw new Error('boom');
      },
      random: () => 1,
      setTimer: (_fn, ms) => timers.push(ms),
    });
    client.start();
    expect(timers).toEqual([1000]);
  });
});

describe('URLs are relative to the document (ingress base path)', () => {
  it('resolves below the ingress prefix', () => {
    const base = 'https://ha.local/api/hassio_ingress/abc123/#/settings';
    expect(apiUrl('api/v1/info', base).toString()).toBe(
      'https://ha.local/api/hassio_ingress/abc123/api/v1/info',
    );
    expect(liveUrl(base)).toBe('wss://ha.local/api/hassio_ingress/abc123/api/v1/live');
    expect(liveUrl('http://127.0.0.1:8099/')).toBe('ws://127.0.0.1:8099/api/v1/live');
  });
  it('works from a path without trailing slash document', () => {
    expect(apiUrl('api/v1/info', 'http://h/app/index.html').toString()).toBe(
      'http://h/app/api/v1/info',
    );
  });
});
