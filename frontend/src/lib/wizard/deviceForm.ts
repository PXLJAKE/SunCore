// SPDX-License-Identifier: Apache-2.0
// Device form generated from a device template: roles, power model, comfort band, protection
// times. The template never contains entity ids; the user maps the roles.
import type {
  ComfortBand,
  ControlSpec,
  DeviceConfig,
  DeviceKind,
  DeviceTemplate,
  PowerModel,
  PowerModelKind,
} from '../api/types';
import { emptyRef, fromRef, slugify, toRef, uniqueId, type RefField } from './forms';

/** The primary entity role a control writes (the first relay of a `relays` control). */
export function controlRoleOf(control: ControlSpec): string {
  return control.kind === 'relays' ? (control.roles[0] ?? '') : control.role;
}

/** Device kinds with a comfort band (core/devices.py THERMAL_KINDS). */
export const THERMAL_KINDS: readonly DeviceKind[] = ['climate', 'water_heater', 'heat_pump'];

/** Comfort band suggested for a device whose template has none; the user can change it. */
export const DEFAULT_COMFORT: ComfortBand = { min_c: 20, max_c: 26 };

export interface DeviceForm {
  /** `null` while the device is new; an existing id never changes. */
  id: string | null;
  name: string;
  templateId: string;
  kind: DeviceKind;
  entities: Record<string, RefField>;
  /** Registry device of the chosen entity per role (scopes `same_device_as` roles). */
  deviceIds: Record<string, string | null>;
  /** Registry device name of the chosen entity per role (ranking hint, duplicate warning). */
  deviceNames: Record<string, string | null>;
  powerKind: PowerModelKind;
  nominalW: number | null;
  /** Fields of the power model the form does not edit. */
  powerRoles: { power: string | null; energy: string | null; counterUpdateS: number | null };
  control: ControlSpec;
  comfortMin: number | null;
  comfortMax: number | null;
  minRunMin: number | null;
  minPauseMin: number | null;
  priority: number;
  /**
   * Protection fields the form does not edit: from the template defaults for a new device, from
   * the stored device when editing. `null` = safety/controller default, never "unlimited".
   */
  protection: Protection;
  /** The stored device: fields the form does not edit (hysteresis, windows, ...) are kept. */
  base: DeviceConfig | null;
}

export interface Protection {
  settle_s: number | null;
  hysteresis_w: number | null;
  hysteresis_delay_s: number | null;
  max_switches_per_hour: number | null;
}

const secToMin = (s: number): number => Math.round((s / 60) * 100) / 100;
const minToSec = (m: number | null): number => Math.max(0, Math.round((m ?? 0) * 60));

/** Which template roles carry a power or an energy measurement (by the matcher's device class). */
export function roleByDeviceClass(
  template: Pick<DeviceTemplate, 'roles'>,
  deviceClass: 'power' | 'energy',
): string[] {
  return Object.entries(template.roles)
    .filter(([, spec]) => spec.matcher.device_class === deviceClass)
    .map(([role]) => role);
}

export interface PowerModelOption {
  kind: PowerModelKind;
  /** The kind can be used with the currently mapped roles. */
  available: boolean;
  needsNominal: boolean;
}

/**
 * Power model kinds and whether the mapped roles allow them: `measured` needs a mapped power
 * role, `counter_derived` a mapped energy role; `learned` and `nominal` need the nominal power.
 */
export function powerModelOptions(
  template: Pick<DeviceTemplate, 'roles'>,
  entities: Record<string, RefField>,
): PowerModelOption[] {
  const mapped = (role: string) => toRef(entities[role] ?? emptyRef()) !== null;
  const hasPower = roleByDeviceClass(template, 'power').some(mapped);
  const hasEnergy = roleByDeviceClass(template, 'energy').some(mapped);
  return [
    { kind: 'measured', available: hasPower, needsNominal: false },
    { kind: 'counter_derived', available: hasEnergy, needsNominal: false },
    { kind: 'learned', available: true, needsNominal: true },
    { kind: 'nominal', available: true, needsNominal: true },
  ];
}

/** The preferred kind for the mapped roles: measured if a power sensor is mapped, else the template default. */
export function preferredPowerKind(
  template: Pick<DeviceTemplate, 'roles' | 'defaults'>,
  entities: Record<string, RefField>,
): PowerModelKind {
  const options = powerModelOptions(template, entities);
  const ok = (k: PowerModelKind) => options.find((o) => o.kind === k)?.available ?? false;
  // A mapped power sensor beats the template default (learned from a counter, nominal): it is
  // the only model that measures. It may come from another device (`power` role of the climate
  // templates) and may lag (smartthinq_sensors polls every 5 minutes); the user can still switch.
  if (ok('measured')) return 'measured';
  const wanted = template.defaults.power_model.kind;
  if (wanted !== 'nominal' && ok(wanted)) return wanted;
  if (ok('counter_derived')) return 'counter_derived';
  return 'nominal';
}

export function newDeviceForm(
  template: DeviceTemplate,
  name: string,
  priority: number,
): DeviceForm {
  const d = template.defaults;
  const entities: Record<string, RefField> = {};
  for (const role of Object.keys(template.roles)) entities[role] = emptyRef();
  const thermal = THERMAL_KINDS.includes(template.device_kind);
  const comfort = d.comfort ?? (thermal ? DEFAULT_COMFORT : null);
  return {
    id: null,
    name,
    templateId: template.id,
    kind: template.device_kind,
    entities,
    deviceIds: {},
    deviceNames: {},
    powerKind: d.power_model.kind,
    nominalW: d.power_model.nominal_w ?? null,
    powerRoles: {
      power: d.power_model.power_role ?? null,
      energy: d.power_model.energy_role ?? null,
      counterUpdateS: d.power_model.counter_update_s ?? null,
    },
    control: structuredClone(d.control),
    comfortMin: comfort?.min_c ?? null,
    comfortMax: comfort?.max_c ?? null,
    minRunMin: secToMin(d.min_run_s),
    minPauseMin: secToMin(d.min_pause_s),
    priority,
    protection: {
      settle_s: d.settle_s ?? null,
      hysteresis_w: d.hysteresis_w ?? null,
      hysteresis_delay_s: d.hysteresis_delay_s ?? null,
      max_switches_per_hour: d.max_switches_per_hour ?? null,
    },
    base: null,
  };
}

export function deviceFormFromConfig(
  cfg: DeviceConfig,
  template: DeviceTemplate | null,
): DeviceForm {
  const entities: Record<string, RefField> = {};
  for (const role of Object.keys(template?.roles ?? {})) entities[role] = emptyRef();
  for (const [role, ref] of Object.entries(cfg.entities ?? {})) entities[role] = fromRef(ref);
  const pm = cfg.power_model;
  return {
    id: cfg.id,
    name: cfg.name,
    templateId: cfg.template_id,
    kind: cfg.kind,
    entities,
    deviceIds: {},
    deviceNames: {},
    powerKind: pm.kind,
    nominalW: pm.nominal_w ?? null,
    powerRoles: {
      power: pm.power_role ?? null,
      energy: pm.energy_role ?? null,
      counterUpdateS: pm.counter_update_s ?? null,
    },
    control: structuredClone(cfg.control),
    comfortMin: cfg.comfort?.min_c ?? null,
    comfortMax: cfg.comfort?.max_c ?? null,
    minRunMin: secToMin(cfg.min_run_s),
    minPauseMin: secToMin(cfg.min_pause_s),
    priority: cfg.priority,
    protection: {
      settle_s: cfg.settle_s ?? null,
      hysteresis_w: cfg.hysteresis_w ?? null,
      hysteresis_delay_s: cfg.hysteresis_delay_s ?? null,
      max_switches_per_hour: cfg.max_switches_per_hour ?? null,
    },
    base: cfg,
  };
}

/** The power model to store: roles follow the chosen kind, the nominal power only when needed. */
export function powerModelFromForm(
  form: DeviceForm,
  template: Pick<DeviceTemplate, 'roles'> | null,
): PowerModel {
  const kind = form.powerKind;
  const mappedIn = (role: string | null) =>
    role !== null && toRef(form.entities[role] ?? emptyRef()) !== null ? role : null;
  const guess = (cls: 'power' | 'energy') =>
    template ? (roleByDeviceClass(template, cls).find((r) => mappedIn(r)) ?? null) : null;
  const powerRole = mappedIn(form.powerRoles.power) ?? guess('power');
  const energyRole = mappedIn(form.powerRoles.energy) ?? guess('energy');
  const withEnergy = kind === 'counter_derived' || kind === 'learned';
  const energy = withEnergy ? energyRole : null;
  return {
    kind,
    power_role: kind === 'measured' || kind === 'learned' ? powerRole : null,
    energy_role: energy,
    nominal_w: kind === 'learned' || kind === 'nominal' ? form.nominalW : null,
    counter_update_s: energy !== null ? form.powerRoles.counterUpdateS : null,
  };
}

export function deviceFromForm(
  form: DeviceForm,
  id: string,
  template: Pick<DeviceTemplate, 'roles'> | null,
): DeviceConfig {
  const entities: DeviceConfig['entities'] = {};
  for (const [role, field] of Object.entries(form.entities)) {
    const ref = toRef(field, !isCounterRole(template, role));
    if (ref) entities[role] = ref;
  }
  const thermal = THERMAL_KINDS.includes(form.kind);
  const comfort: ComfortBand | null =
    thermal && form.comfortMin != null && form.comfortMax != null
      ? { min_c: form.comfortMin, max_c: form.comfortMax }
      : null;
  const base = form.base;
  return {
    windows: base?.windows ?? [],
    // Phase 3 fields (plan WP2): kept from the stored device; the editor UI follows in WP10.
    allow_switch_on: base?.allow_switch_on ?? false,
    switch_on_hvac_mode: base?.switch_on_hvac_mode ?? null,
    override_policy: base?.override_policy ?? {
      kind: 'next_day',
      resume_time: '04:00:00',
      hours: null,
    },
    ...(base ?? {}),
    ...form.protection,
    id,
    template_id: form.templateId,
    name: form.name.trim(),
    kind: form.kind,
    entities,
    power_model: powerModelFromForm(form, template),
    control: form.control,
    priority: form.priority,
    min_run_s: minToSec(form.minRunMin),
    min_pause_s: minToSec(form.minPauseMin),
    comfort,
  };
}

function isCounterRole(template: Pick<DeviceTemplate, 'roles'> | null, role: string): boolean {
  return template?.roles[role]?.matcher.device_class === 'energy';
}

/** Checks the form can do without the server; the rest comes back as 422 issues. */
export type DeviceFormError =
  | { key: 'name' }
  | { key: 'role_missing'; role: string }
  | { key: 'nominal_missing' }
  | { key: 'comfort_missing' }
  | { key: 'comfort_order' }
  | { key: 'power_model_unavailable' };

export function validateDeviceForm(form: DeviceForm, template: DeviceTemplate): DeviceFormError[] {
  const errors: DeviceFormError[] = [];
  if (form.name.trim() === '') errors.push({ key: 'name' });
  for (const [role, spec] of Object.entries(template.roles)) {
    if (spec.required && toRef(form.entities[role] ?? emptyRef()) === null) {
      errors.push({ key: 'role_missing', role });
    }
  }
  const option = powerModelOptions(template, form.entities).find((o) => o.kind === form.powerKind);
  if (option && !option.available) errors.push({ key: 'power_model_unavailable' });
  if (option?.needsNominal && (form.nominalW == null || form.nominalW <= 0)) {
    errors.push({ key: 'nominal_missing' });
  }
  const needsComfort = THERMAL_KINDS.includes(form.kind) && form.control.kind === 'setpoint';
  if (needsComfort && (form.comfortMin == null || form.comfortMax == null)) {
    errors.push({ key: 'comfort_missing' });
  } else if (
    form.comfortMin != null &&
    form.comfortMax != null &&
    !(form.comfortMin < form.comfortMax)
  ) {
    errors.push({ key: 'comfort_order' });
  }
  return errors;
}

/** Registry device of an entity, as the climate candidates list it. */
export interface UnitInfo {
  deviceId: string | null;
  deviceName: string | null;
  platform: string | null;
}

/** Case- and whitespace-insensitive device name (same rule as the backend system check). */
export const normaliseName = (name: string | null | undefined): string =>
  (name ?? '').toLocaleLowerCase().split(/\s+/).filter(Boolean).join(' ');

/**
 * Whether the chosen control entity looks like a unit that another configured climate device
 * already controls: the same entity, or an entity of another registry device on another
 * platform (integration) with the same device name (two integrations for one air conditioner).
 * Two devices of one integration with equal names are not flagged; renaming a device in Home
 * Assistant silences the hint. Only a hint; never blocks saving.
 */
export function seemsConfiguredAlready(
  form: Pick<DeviceForm, 'id' | 'kind' | 'entities' | 'control'>,
  others: readonly DeviceConfig[],
  lookup: ReadonlyMap<string, UnitInfo>,
): boolean {
  if (form.kind !== 'climate') return false;
  const chosen = toRef(form.entities[controlRoleOf(form.control)] ?? emptyRef())?.entity_id;
  if (!chosen) return false;
  const mine = lookup.get(chosen);
  return others.some((d) => {
    if (d.id === form.id || d.kind !== 'climate') return false;
    const other = d.entities?.[controlRoleOf(d.control)]?.entity_id;
    if (!other) return false;
    if (other === chosen) return true;
    const theirs = lookup.get(other);
    const name = normaliseName(mine?.deviceName);
    return (
      name !== '' &&
      theirs !== undefined &&
      mine !== undefined &&
      theirs.deviceId !== mine.deviceId &&
      theirs.platform !== null &&
      mine.platform !== null &&
      theirs.platform !== mine.platform &&
      normaliseName(theirs.deviceName) === name
    );
  });
}

/** Device id for a new device: slug of the name, unique among `taken`. */
export function newDeviceId(name: string, taken: ReadonlySet<string>): string {
  return uniqueId(slugify(name), taken);
}

/** Name for a new device of a template: the translated template name, numbered if taken. */
export function uniqueName(base: string, taken: readonly string[]): string {
  const names = new Set(taken.map((n) => n.toLowerCase()));
  if (!names.has(base.toLowerCase())) return base;
  for (let n = 2; ; n += 1) {
    const candidate = `${base} ${n}`;
    if (!names.has(candidate.toLowerCase())) return candidate;
  }
}
