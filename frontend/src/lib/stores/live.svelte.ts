// SPDX-License-Identifier: Apache-2.0
// One WebSocket subscription feeds every view (docs/architecture.md section 9).
import { liveUrl } from '../api/client';
import type { Decision, Flows, Health, LiveMessage } from '../api/types';
import { LiveClient, type ConnectionState } from '../api/ws';

export type LinkStatus = 'connecting' | 'live' | 'ha_down' | 'offline';

/** HA connector states that count as "connected" (suncore.ha.connection.ConnectionState). */
const HA_CONNECTED = 'connected';

export function linkStatus(
  connection: ConnectionState,
  haveSnapshot: boolean,
  health: Health | null,
): LinkStatus {
  if (connection === 'closed') return 'offline';
  if (connection === 'connecting' || !haveSnapshot) return 'connecting';
  if (!health || health.ha_state !== HA_CONNECTED || !health.ok) return 'ha_down';
  return 'live';
}

export class LiveStore {
  flows = $state.raw<Flows | null>(null);
  health = $state.raw<Health | null>(null);
  decisions = $state.raw<Decision[]>([]);
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
        this.haveSnapshot = true;
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
    }
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
  }

  stop(): void {
    this.client?.stop();
    this.client = null;
  }
}

export const live = new LiveStore();
