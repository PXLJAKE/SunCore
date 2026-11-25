// SPDX-License-Identifier: Apache-2.0
// Logic of the Phase 6 settings fields (7-Q): per-device `para14a` and `shed_rank`, the island
// keep list (`grid.backup.keep_devices`) and the manual MQTT broker. Field names, ranges and
// defaults mirror the backend models (`core/devices.py`, `core/grid.py`, `core/config.py`); the
// backend validates, these helpers only build bodies and keep secrets out of the browser state.
import type { DeviceConfig, MqttConfig, SiteConfig } from '../api/types';

export const SHED_RANK_MIN = 0;
export const SHED_RANK_MAX = 10_000;
export const MQTT_PORT_MIN = 1;
export const MQTT_PORT_MAX = 65_535;
export const MQTT_DEFAULT_PORT = 1883;
/** What the API returns for a stored password (`SECRET_MASK`); sent back it keeps the secret. */
export const SECRET_MASK = '**********';

/** The two per-device grid fields; `shedRank: null` means "reverse priority" (backend default). */
export interface DeviceGridEdit {
  para14a: boolean;
  shedRank: number | null;
}

export function deviceGridEdit(d: Pick<DeviceConfig, 'para14a' | 'shed_rank'>): DeviceGridEdit {
  return { para14a: d.para14a ?? false, shedRank: d.shed_rank ?? null };
}

/** True when the edit differs from the stored device. */
export function deviceGridChanged(
  d: Pick<DeviceConfig, 'para14a' | 'shed_rank'>,
  edit: DeviceGridEdit,
): boolean {
  const base = deviceGridEdit(d);
  return base.para14a !== edit.para14a || base.shedRank !== edit.shedRank;
}

/** The stored devices with the edits applied; every other field stays as stored. */
export function applyDeviceGridEdits(
  devices: readonly DeviceConfig[],
  edits: Readonly<Record<string, DeviceGridEdit>>,
): DeviceConfig[] {
  return devices.map((d) => {
    const e = edits[d.id];
    return e ? { ...d, para14a: e.para14a, shed_rank: e.shedRank } : d;
  });
}

/** The island keep list of the site, or `null` while no backup entity is configured. */
export function keepDevices(site: SiteConfig | null): string[] | null {
  const backup = site?.grid.backup;
  return backup ? [...(backup.keep_devices ?? [])] : null;
}

/**
 * Toggle a device in the keep list. Ids of devices that no longer exist stay in the list: the
 * backend does not reference-check them and silently dropping a stored entry is not ours to do.
 */
export function toggleKeep(keep: readonly string[], id: string, on: boolean): string[] {
  const without = keep.filter((x) => x !== id);
  return on ? [...without, id] : without;
}

/** The site with a new keep list; `null` when there is no backup entity to attach it to. */
export function siteWithKeep(site: SiteConfig, keep: readonly string[]): SiteConfig | null {
  const backup = site.grid.backup;
  if (!backup) return null;
  return { ...site, grid: { ...site.grid, backup: { ...backup, keep_devices: [...keep] } } };
}

/**
 * The site with the keep list reduced to devices that exist before and after a change of the
 * device list, or `null` when nothing changes. A kept id that is gone (or was already stale) is
 * dropped, so a later device with the same id never inherits "kept in island operation".
 */
export function pruneKeep(
  site: SiteConfig | null,
  before: readonly string[],
  after: readonly string[],
): SiteConfig | null {
  const keep = keepDevices(site);
  if (!site || keep === null) return null;
  const next = keep.filter((id) => before.includes(id) && after.includes(id));
  return next.length === keep.length ? null : siteWithKeep(site, next);
}

// --- manual MQTT broker -----------------------------------------------------------------

export interface MqttBrokerForm {
  broker: 'supervisor' | 'manual';
  host: string;
  port: number | null;
  tls: boolean;
  username: string;
  /** Typed new password. Never filled from the server; empty = keep what is stored. */
  password: string;
  /** Remove the stored password (only with a stored one). */
  clearPassword: boolean;
}

export function mqttFormFromConfig(c: MqttConfig): MqttBrokerForm {
  return {
    broker: c.broker,
    host: c.host ?? '',
    port: c.port ?? MQTT_DEFAULT_PORT,
    tls: c.tls ?? false,
    username: c.username ?? '',
    password: '',
    clearPassword: false,
  };
}

/** A password is stored (the API only ever returns the mask, never the secret). */
export function hasStoredPassword(c: Pick<MqttConfig, 'password'> | null): boolean {
  return c?.password != null && c.password !== '';
}

/**
 * The section body of a save. The stored password is never read: an empty field sends the mask
 * (the backend keeps the stored secret), a typed one replaces it, "remove" sends none. The
 * Supervisor broker takes no host or credentials (the backend rejects extras), so they are
 * cleared; the port and TLS keep their stored values then.
 */
export function mqttFromForm(
  form: MqttBrokerForm,
  stored: MqttConfig,
  hadPassword: boolean,
): MqttConfig {
  if (form.broker === 'supervisor') {
    return { ...stored, broker: 'supervisor', host: null, username: null, password: null };
  }
  const username = form.username.trim();
  let password: string | null = null;
  if (!form.clearPassword) {
    if (form.password !== '') password = form.password;
    else if (hadPassword) password = SECRET_MASK;
  }
  return {
    ...stored,
    broker: 'manual',
    host: form.host.trim() === '' ? null : form.host.trim(),
    port: form.port ?? MQTT_DEFAULT_PORT,
    tls: form.tls,
    username: username === '' ? null : username,
    password,
  };
}

export function mqttDirty(form: MqttBrokerForm, stored: MqttConfig): boolean {
  const base = mqttFormFromConfig(stored);
  return (
    form.password !== '' ||
    form.clearPassword ||
    form.broker !== base.broker ||
    form.host !== base.host ||
    form.port !== base.port ||
    form.tls !== base.tls ||
    form.username !== base.username
  );
}
