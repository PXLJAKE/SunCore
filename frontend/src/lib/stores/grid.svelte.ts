// SPDX-License-Identifier: Apache-2.0
// Grid status for the Now badges (island, §14a, month peak); read every 15 s while shown.
import { flexApi } from '../api/client';
import type { GridStatus } from '../api/types';

export class GridStore {
  status = $state.raw<GridStatus | null>(null);

  async refresh(): Promise<void> {
    try {
      this.status = await flexApi.gridStatus();
    } catch {
      // keep the last value: the badges are informational
    }
  }
}

export const grid = new GridStore();
