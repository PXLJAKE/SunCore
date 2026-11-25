// SPDX-License-Identifier: Apache-2.0
// One lock for every write to the `mqtt` section (broker form and the mode-raise opt-in), and
// every write is built from the server's latest copy. Two saves can no longer overlap, and a
// broker save can no longer send an old `allow_mode_raise` that silently turns the opt-in back
// on (safety review of 7-Q, F1).
import { api } from '../api/client';
import type { MqttConfig } from '../api/types';

/** What the section is while nothing is stored (broker from the Supervisor). */
export const MQTT_DEFAULTS: MqttConfig = {
  schema_version: 1,
  enabled: true,
  broker: 'supervisor',
  port: 1883,
  tls: false,
  discovery_prefix: 'homeassistant',
  allow_mode_raise: false,
};

class MqttSaveLock {
  /** True while a save runs; the controls of both forms are disabled meanwhile. */
  busy = $state(false);

  /**
   * Read the latest section, build the body from it and store it. Returns `null` (nothing sent)
   * when another save is running. Errors of the read or the write propagate to the caller.
   */
  async save(build: (latest: MqttConfig) => MqttConfig): Promise<MqttConfig | null> {
    if (this.busy) return null;
    this.busy = true;
    try {
      const latest = (await api.getSection<MqttConfig>('mqtt')) ?? MQTT_DEFAULTS;
      return await api.putSection<MqttConfig>('mqtt', build(latest));
    } finally {
      this.busy = false;
    }
  }
}

export const mqttLock = new MqttSaveLock();
