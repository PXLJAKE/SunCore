// SPDX-License-Identifier: Apache-2.0
// Actions on the control layer (emergency stop, modes, overrides, proposals). The state itself
// arrives over the one WebSocket (LiveStore.control); after an action the control state is read
// once so the page does not wait for the next tick, and a stale identity token is refreshed.
import { api } from '../api/client';
import { describeError } from '../api/errors';
import type { DeviceControlState, OperatingMode, OverrideCommand } from '../api/types';
import { live } from './live.svelte';

/** Scope of an error message: `global`, `emergency`, or a device id / intent id. */
export type ErrorScope = string;

/** What the user decided in the confirmation dialog: the token is the one they were shown. */
export interface ModeRequest {
  deviceId: string;
  mode: OperatingMode;
  identity: string;
}

export class ControlActions {
  /** Catalogue key of the last failed action per scope. */
  errors = $state<Record<ErrorScope, string>>({});
  /** Scopes with a request in flight. */
  busy = $state<Record<ErrorScope, boolean>>({});

  clearError(scope: ErrorScope): void {
    if (scope in this.errors) {
      const rest = { ...this.errors };
      delete rest[scope];
      this.errors = rest;
    }
  }

  private async run(scope: ErrorScope, fn: () => Promise<unknown>): Promise<boolean> {
    if (this.busy[scope]) return false;
    this.busy = { ...this.busy, [scope]: true };
    this.clearError(scope);
    try {
      await fn();
      return true;
    } catch (e) {
      this.errors = { ...this.errors, [scope]: describeError(e).key };
      return false;
    } finally {
      const rest = { ...this.busy };
      delete rest[scope];
      this.busy = rest;
      await this.refresh();
    }
  }

  /** Read the control state once; the live feed keeps it current afterwards. */
  async refresh(): Promise<void> {
    try {
      live.setControl(await api.controlState());
    } catch {
      // the next WebSocket message brings the state
    }
  }

  emergencyStop(): Promise<boolean> {
    return this.run('emergency', () => api.emergencyStop());
  }

  clearEmergencyStop(): Promise<boolean> {
    return this.run('emergency', () => api.clearEmergencyStop());
  }

  setGlobalMode(mode: OperatingMode): Promise<boolean> {
    return this.run('global', () => api.setGlobalMode(mode));
  }

  /**
   * Set one device's mode. Anything above shadow sends the identity token the user saw when
   * they decided (a re-mapped device answers 409 and keeps its mode); shadow needs none.
   */
  setDeviceMode(req: ModeRequest): Promise<boolean> {
    return this.run(req.deviceId, () =>
      api.setDeviceMode(req.deviceId, req.mode, req.mode === 'shadow' ? null : req.identity),
    );
  }

  override(
    device: Pick<DeviceControlState, 'device_id'>,
    command: OverrideCommand,
  ): Promise<boolean> {
    return this.run(device.device_id, () => api.override(device.device_id, command));
  }

  confirmProposal(intentId: string): Promise<boolean> {
    return this.run(intentId, () => api.confirmProposal(intentId));
  }

  dismissProposal(intentId: string): Promise<boolean> {
    return this.run(intentId, () => api.dismissProposal(intentId));
  }
}

export const control = new ControlActions();
