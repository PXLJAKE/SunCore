// SPDX-License-Identifier: Apache-2.0
// EV mode/target and program jobs per device (PR 6-M). The REST endpoints change only the state
// the controller reads; every device write still passes the safety gate on the server.
import { flexApi } from '../api/client';
import { describeError } from '../api/errors';
import type { EvMode, EvTarget, FlexView } from '../api/types';
import { control } from './control.svelte';

export class FlexStore {
  views = $state<Record<string, FlexView>>({});
  errors = $state<Record<string, string>>({});
  busy = $state<Record<string, boolean>>({});

  async load(deviceId: string): Promise<void> {
    try {
      const view = await flexApi.flex(deviceId);
      this.views = { ...this.views, [deviceId]: view };
    } catch (e) {
      this.fail(deviceId, e);
    }
  }

  clearError(deviceId: string): void {
    if (deviceId in this.errors) {
      const rest = { ...this.errors };
      delete rest[deviceId];
      this.errors = rest;
    }
  }

  private fail(deviceId: string, e: unknown): void {
    const failed = describeError(e);
    this.errors = { ...this.errors, [deviceId]: failed.issues[0]?.key ?? failed.key };
  }

  private async run(deviceId: string, fn: () => Promise<unknown>): Promise<boolean> {
    if (this.busy[deviceId]) return false;
    this.busy = { ...this.busy, [deviceId]: true };
    this.clearError(deviceId);
    let ok = true;
    try {
      await fn();
    } catch (e) {
      this.fail(deviceId, e);
      ok = false;
    }
    const rest = { ...this.busy };
    delete rest[deviceId];
    this.busy = rest;
    await this.load(deviceId);
    await control.refresh();
    return ok;
  }

  setEv(deviceId: string, mode: EvMode, target: EvTarget | null): Promise<boolean> {
    return this.run(deviceId, () => flexApi.setEv(deviceId, mode, target));
  }

  arm(deviceId: string, deadlineIso: string): Promise<boolean> {
    return this.run(deviceId, () => flexApi.armProgram(deviceId, deadlineIso));
  }

  cancel(deviceId: string): Promise<boolean> {
    return this.run(deviceId, () => flexApi.cancelProgram(deviceId));
  }
}

export const flex = new FlexStore();
