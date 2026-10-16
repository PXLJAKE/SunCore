// SPDX-License-Identifier: Apache-2.0
// Live WebSocket client: typed messages, reconnect with jittered exponential back-off.
import type { LiveMessage } from './types';

export type ConnectionState = 'connecting' | 'open' | 'closed';

export const BACKOFF_BASE_MS = 1000;
export const BACKOFF_CAP_MS = 30_000;

/**
 * Delay before reconnect attempt `attempt` (0-based): exponential growth capped at
 * `BACKOFF_CAP_MS`, then "equal jitter" (half fixed, half random) so many clients that lost
 * the same server do not reconnect in lock step.
 */
export function backoffDelay(attempt: number, random: () => number = Math.random): number {
  const exp = Math.min(BACKOFF_CAP_MS, BACKOFF_BASE_MS * 2 ** Math.max(0, attempt));
  return Math.round(exp / 2 + (exp / 2) * random());
}

const KNOWN_TYPES = new Set(['snapshot', 'flows', 'health', 'decisions', 'control']);

/** Parse and shallowly validate one frame; unknown or malformed frames yield `null`. */
export function parseMessage(data: unknown): LiveMessage | null {
  if (typeof data !== 'string') return null;
  try {
    const msg = JSON.parse(data) as { type?: unknown };
    if (typeof msg === 'object' && msg !== null && KNOWN_TYPES.has(msg.type as string)) {
      return msg as LiveMessage;
    }
  } catch {
    // ignore malformed frame
  }
  return null;
}

export interface SocketLike {
  onopen: ((ev: unknown) => void) | null;
  onmessage: ((ev: { data: unknown }) => void) | null;
  onclose: ((ev: unknown) => void) | null;
  onerror: ((ev: unknown) => void) | null;
  close(): void;
}

export interface LiveClientOptions {
  url: string | (() => string);
  onMessage: (msg: LiveMessage) => void;
  onState: (state: ConnectionState) => void;
  createSocket?: (url: string) => SocketLike;
  random?: () => number;
  setTimer?: (fn: () => void, ms: number) => unknown;
  clearTimer?: (handle: unknown) => void;
}

export class LiveClient {
  private socket: SocketLike | null = null;
  private timer: unknown = null;
  private attempt = 0;
  private stopped = true;
  private readonly createSocket: (url: string) => SocketLike;
  private readonly random: () => number;
  private readonly setTimer: (fn: () => void, ms: number) => unknown;
  private readonly clearTimer: (handle: unknown) => void;

  constructor(private readonly opts: LiveClientOptions) {
    this.createSocket = opts.createSocket ?? ((u) => new WebSocket(u) as unknown as SocketLike);
    this.random = opts.random ?? Math.random;
    this.setTimer = opts.setTimer ?? ((fn, ms) => setTimeout(fn, ms));
    this.clearTimer = opts.clearTimer ?? ((h) => clearTimeout(h as number));
  }

  start(): void {
    if (!this.stopped) return;
    this.stopped = false;
    this.attempt = 0;
    this.connect();
  }

  stop(): void {
    this.stopped = true;
    if (this.timer !== null) this.clearTimer(this.timer);
    this.timer = null;
    const sock = this.socket;
    this.socket = null;
    if (sock) {
      sock.onopen = sock.onmessage = sock.onclose = sock.onerror = null;
      sock.close();
    }
    this.opts.onState('closed');
  }

  /**
   * Skip the back-off wait and reconnect immediately (network came back, tab became visible).
   * No-op while connected or connecting, or after stop().
   */
  reconnectNow(): void {
    if (this.stopped || this.socket !== null) return;
    if (this.timer !== null) this.clearTimer(this.timer);
    this.timer = null;
    this.attempt = 0;
    this.connect();
  }

  private connect(): void {
    this.timer = null;
    this.opts.onState('connecting');
    const url = typeof this.opts.url === 'function' ? this.opts.url() : this.opts.url;
    let sock: SocketLike;
    try {
      sock = this.createSocket(url);
    } catch {
      this.scheduleReconnect();
      return;
    }
    this.socket = sock;
    sock.onopen = () => this.opts.onState('open');
    sock.onmessage = (ev) => {
      const msg = parseMessage(ev.data);
      if (!msg) return;
      // A message proves the feed works; only now is the back-off reset.
      this.attempt = 0;
      this.opts.onMessage(msg);
    };
    sock.onerror = () => {
      // `onclose` follows; reconnect is scheduled there.
    };
    sock.onclose = () => {
      if (this.socket !== sock) return;
      this.socket = null;
      this.scheduleReconnect();
    };
  }

  private scheduleReconnect(): void {
    if (this.stopped) return;
    this.opts.onState('closed');
    const delay = backoffDelay(this.attempt, this.random);
    this.attempt += 1;
    this.timer = this.setTimer(() => this.connect(), delay);
  }
}
