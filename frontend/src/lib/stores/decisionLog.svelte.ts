// SPDX-License-Identifier: Apache-2.0
// The decision log view: server-side filtered by device and kind; a slower answer of an older
// filter never replaces the answer of the current one.
import { api } from '../api/client';
import { describeError } from '../api/errors';
import type { Decision, DecisionKind } from '../api/types';

export const DECISION_KINDS: readonly DecisionKind[] = [
  'accepted',
  'proposed',
  'shadow',
  'held',
  'rejected',
  'failed',
];

export const LOG_LIMIT = 200;

export class DecisionLogStore {
  items = $state.raw<Decision[]>([]);
  loading = $state(false);
  error = $state<string | null>(null);
  /** Empty string = all devices. */
  deviceId = $state('');
  /** Empty = all kinds. */
  kinds = $state<DecisionKind[]>([]);

  private seq = 0;

  toggleKind(kind: DecisionKind): void {
    this.kinds = this.kinds.includes(kind)
      ? this.kinds.filter((k) => k !== kind)
      : [...this.kinds, kind];
  }

  async load(): Promise<void> {
    const mine = ++this.seq;
    this.loading = true;
    try {
      const items = await api.decisions({
        limit: LOG_LIMIT,
        deviceId: this.deviceId || undefined,
        kinds: this.kinds,
      });
      if (mine !== this.seq) return;
      this.items = items;
      this.error = null;
    } catch (e) {
      if (mine !== this.seq) return;
      this.error = describeError(e).key;
    } finally {
      if (mine === this.seq) this.loading = false;
    }
  }
}
