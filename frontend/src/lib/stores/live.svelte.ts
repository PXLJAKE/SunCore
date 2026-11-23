// SPDX-License-Identifier: Apache-2.0
// One WebSocket subscription feeds every view (docs/architecture.md section 9).
import { liveUrl } from '../api/client';
import type { ControlState, Decision, Flows, Health, LiveMessage, PlanSummary } from '../api/types';
import { LiveClient, type ConnectionState } from '../api/ws';

export type LinkStatus = 'connecting' | 'live' | 'ha_down' | 'degraded' | 'offline';

/** HA connector states that count as "connected" (suncore.ha.connection.ConnectionState). */
const HA_CONNECTED = 'connected';

export function linkStatus(
  connection: ConnectionState,
  haveSnapshot: boolean,
  health: Health | null,
): LinkStatus {
  if (connection === 'closed') return 'offline';
  if (connection === 'connecting' || !haveSnapshot) return 'connecting';
  if (!health || health.ha_state !== HA_CONNECTED) return 'ha_down';
  // Home Assistant is fine but SunCore itself reports trouble (e.g. database).
  if (!health.ok || !health.db_ok) return 'degraded';
  return 'live';
}

export class LiveStore {
  flows = $state.raw<Flows | null>(null);
  health = $state.raw<Health | null>(null);
  decisions = $state.raw<Decision[]>([]);
  /** Control layer state (devices, site surplus, proposals). */
  control = $state.raw<ControlState | null>(null);
  /** Summary of the newest plan announced on the feed (the intervals are fetched on demand). */
  plan = $state.raw<PlanSummary | null>(null);
  /** Plan messages received since start: views refetch the plan when it changes. */
  planUpdates = $state(0);
  /** Snapshots received since start: one per (re)connect, so views refetch what they cached. */
  snapshotCount = $state(0);
  connection = $state<ConnectionState>('connecting');
  haveSnapshot = $state(false);
  /** Flow messages received since start (tests and the E2E "values update" check). */
  flowUpdates = $state(0);

  status = $derived(linkStatus(this.connection, this.haveSnapshot, this.health));

  private client: LiveClient | null = null;

  apply(msg: LiveMessage): void {
    switch (msg.type) {
      case 'snapshot':
        this.flows = msg.flows;
        this.health = msg.health;
        this.decisions = msg.decisions;
        this.control = msg.control ?? null;
        this.haveSnapshot = true;
        this.snapshotCount += 1;
        this.flowUpdates += 1;
        break;
      case 'flows':
        this.flows = msg.flows;
        this.flowUpdates += 1;
        break;
      case 'health':
        this.health = msg.health;
        break;
      case 'decisions':
        this.decisions = msg.decisions;
        break;
      case 'control':
        this.control = msg.control;
        break;
      case 'plan':
        this.plan = msg.plan;
        this.planUpdates += 1;
        break;
    }
  }

  /** A fresher control state than the last WebSocket message (answer of a REST action). */
  setControl(control: ControlState): void {
    this.control = control;
  }

  setConnection(state: ConnectionState): void {
    this.connection = state;
    // After a drop the next snapshot re-validates the data; until then do not claim "live".
    if (state !== 'open') this.haveSnapshot = false;
  }

  start(): void {
    if (this.client) return;
    this.client = new LiveClient({
      url: () => liveUrl(),
      onMessage: (m) => this.apply(m),
      onState: (s) => this.setConnection(s),
    });
    this.client.start();
    if (typeof window !== 'undefined') {
      window.addEventListener('online', this.onWake);
      document.addEventListener('visibilitychange', this.onWake);
    }
  }

  private onWake = (): void => {
    if (typeof document !== 'undefined' && document.hidden) return;
    this.client?.reconnectNow();
  };

  stop(): void {
    if (typeof window !== 'undefined') {
      window.removeEventListener('online', this.onWake);
      document.removeEventListener('visibilitychange', this.onWake);
    }
    this.client?.stop();
    this.client = null;
  }
}

export const live = new LiveStore();
