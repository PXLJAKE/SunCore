// SPDX-License-Identifier: Apache-2.0
// Site section (grid, PV, battery, house) <-> form model, and the application of Energy
// dashboard suggestions. The form keeps text-free primitives; the backend validates (422).
import type { PvArray, SiteConfig, Suggestion } from '../api/types';
import { emptyRef, fromRef, toRef, type RefField } from './forms';

export type GridMode = 'single' | 'pair';
export type ExportLimitMode = 'none' | 'w' | 'pct';

export interface GridForm {
  mode: GridMode;
  power: RefField;
  importPower: RefField;
  exportPower: RefField;
  importLimitW: number | null;
  exportLimitMode: ExportLimitMode;
  exportLimitW: number | null;
  exportLimitPct: number | null;
  para14a: boolean;
  para14aSignal: RefField;
  energyImport: RefField;
  energyExport: RefField;
}

export interface PvRow {
  name: string;
  power: RefField;
  kwp: number | null;
  azimuth: number | null;
  tilt: number | null;
  inverter: string;
}

export interface BatteryForm {
  enabled: boolean;
  power: RefField;
  soc: RefField;
  capacityKwh: number | null;
  minSoc: number | null;
  maxSoc: number | null;
  maxChargeW: number | null;
  maxDischargeW: number | null;
}

export interface SiteForm {
  schemaVersion: number;
  grid: GridForm;
  pv: PvRow[];
  battery: BatteryForm;
  house: RefField;
}

export const emptyGrid = (): GridForm => ({
  mode: 'single',
  power: emptyRef(),
  importPower: emptyRef(),
  exportPower: emptyRef(),
  importLimitW: null,
  exportLimitMode: 'none',
  exportLimitW: null,
  exportLimitPct: null,
  para14a: false,
  para14aSignal: emptyRef(),
  energyImport: emptyRef(),
  energyExport: emptyRef(),
});

export const emptyPvRow = (name = ''): PvRow => ({
  name,
  power: emptyRef(),
  kwp: null,
  azimuth: null,
  tilt: null,
  inverter: '',
});

export const emptyBattery = (): BatteryForm => ({
  enabled: false,
  power: emptyRef(),
  soc: emptyRef(),
  capacityKwh: null,
  minSoc: null,
  maxSoc: null,
  maxChargeW: null,
  maxDischargeW: null,
});

export function emptySiteForm(): SiteForm {
  return {
    schemaVersion: 2,
    grid: emptyGrid(),
    pv: [],
    battery: emptyBattery(),
    house: emptyRef(),
  };
}

/** Wh <-> kWh without float noise (10000 Wh = 10 kWh). */
const whToKwh = (wh: number): number => Math.round(wh) / 1000;
const kwhToWh = (kwh: number): number => Math.round(kwh * 1000);

export function siteFormFromSite(site: SiteConfig | null): SiteForm {
  if (!site) return emptySiteForm();
  const g = site.grid;
  const pair = !g.power && (!!g.import_power || !!g.export_power);
  const limitMode: ExportLimitMode =
    g.export_limit_w != null ? 'w' : g.export_limit_pct_kwp != null ? 'pct' : 'none';
  const b = site.battery;
  return {
    schemaVersion: site.schema_version,
    grid: {
      mode: pair ? 'pair' : 'single',
      power: fromRef(g.power),
      importPower: fromRef(g.import_power),
      exportPower: fromRef(g.export_power),
      importLimitW: g.import_limit_w ?? null,
      exportLimitMode: limitMode,
      exportLimitW: g.export_limit_w ?? null,
      exportLimitPct: g.export_limit_pct_kwp ?? null,
      para14a: g.para14a ?? false,
      para14aSignal: fromRef(g.para14a_signal),
      energyImport: fromRef(g.energy_import),
      energyExport: fromRef(g.energy_export),
    },
    pv: (site.pv ?? []).map((a) => ({
      name: a.name,
      power: fromRef(a.power),
      kwp: a.kwp ?? null,
      azimuth: a.azimuth_deg ?? null,
      tilt: a.tilt_deg ?? null,
      inverter: a.inverter ?? '',
    })),
    battery: b
      ? {
          enabled: true,
          power: fromRef(b.power),
          soc: fromRef(b.soc),
          capacityKwh: b.capacity_wh != null ? whToKwh(b.capacity_wh) : null,
          minSoc: b.min_soc_pct ?? null,
          maxSoc: b.max_soc_pct ?? null,
          maxChargeW: b.max_charge_w ?? null,
          maxDischargeW: b.max_discharge_w ?? null,
        }
      : emptyBattery(),
    house: fromRef(site.house),
  };
}

/** Is there enough to store a site at all (the backend requires a grid sensor or a pair)? */
export function isSiteSavable(form: SiteForm): boolean {
  const g = form.grid;
  return g.mode === 'single'
    ? toRef(g.power) !== null
    : toRef(g.importPower) !== null && toRef(g.exportPower) !== null;
}

/** Client-side checks that would otherwise make `siteFromForm` drop data silently. */
export type SiteFormError = 'grid_pair_incomplete' | 'battery_incomplete' | 'grid_missing';

export function validateSiteForm(form: SiteForm): SiteFormError[] {
  const errors: SiteFormError[] = [];
  const g = form.grid;
  if (g.mode === 'pair' && (toRef(g.importPower) === null) !== (toRef(g.exportPower) === null)) {
    errors.push('grid_pair_incomplete');
  } else if (!isSiteSavable(form)) {
    errors.push('grid_missing');
  }
  const b = form.battery;
  if (b.enabled && (toRef(b.power) === null) !== (toRef(b.soc) === null)) {
    errors.push('battery_incomplete');
  }
  return errors;
}

export function siteFromForm(form: SiteForm): SiteConfig {
  const g = form.grid;
  const single = g.mode === 'single';
  const pv: PvArray[] = [];
  const used = new Set<string>();
  form.pv.forEach((row, i) => {
    const power = toRef(row.power);
    const hasData = power !== null || row.kwp != null || row.name.trim() !== '';
    if (!hasData) return;
    let name = row.name.trim() || `PV ${i + 1}`;
    // Names must be unique; a duplicate gets a number instead of failing the whole save.
    for (let n = 2; used.has(name); n += 1) name = `${row.name.trim() || `PV ${i + 1}`} ${n}`;
    used.add(name);
    pv.push({
      name,
      power,
      kwp: row.kwp,
      azimuth_deg: row.azimuth,
      tilt_deg: row.tilt,
      inverter: row.inverter.trim() === '' ? null : row.inverter.trim(),
    });
  });
  const b = form.battery;
  const bPower = toRef(b.power);
  const bSoc = toRef(b.soc, false);
  return {
    schema_version: form.schemaVersion,
    grid: {
      power: single ? toRef(g.power) : null,
      import_power: single ? null : toRef(g.importPower),
      export_power: single ? null : toRef(g.exportPower),
      import_limit_w: g.importLimitW,
      export_limit_w: g.exportLimitMode === 'w' ? g.exportLimitW : null,
      export_limit_pct_kwp: g.exportLimitMode === 'pct' ? g.exportLimitPct : null,
      para14a: g.para14a,
      para14a_signal: g.para14a ? toRef(g.para14aSignal, false) : null,
      energy_import: toRef(g.energyImport, false),
      energy_export: toRef(g.energyExport, false),
    },
    pv,
    battery:
      b.enabled && bPower && bSoc
        ? {
            power: bPower,
            soc: bSoc,
            capacity_wh: b.capacityKwh != null ? kwhToWh(b.capacityKwh) : null,
            min_soc_pct: b.minSoc,
            max_soc_pct: b.maxSoc,
            max_charge_w: b.maxChargeW,
            max_discharge_w: b.maxDischargeW,
            control: 'observe',
          }
        : null,
    house: toRef(form.house),
  };
}

// --- Energy dashboard suggestions --------------------------------------------------

/** `pv.0.name` and `pv.0.power` describe one array: the UI shows one line for it. */
export function visibleSuggestions(suggestions: readonly Suggestion[]): Suggestion[] {
  return suggestions.filter((s) => !/^pv\.\d+\.name$/.test(s.path));
}

const isRef = (v: unknown): v is { entity_id: string; invert_sign?: boolean } =>
  typeof v === 'object' &&
  v !== null &&
  typeof (v as { entity_id?: unknown }).entity_id === 'string';

const refOf = (v: unknown): RefField | null =>
  isRef(v) ? { entity_id: v.entity_id, invert_sign: v.invert_sign ?? false } : null;

/** Apply one suggestion to the form; returns `false` for a path the form does not know. */
export function applySuggestion(form: SiteForm, path: string, value: unknown): boolean {
  const ref = refOf(value);
  const refFields: Record<string, () => RefField> = {
    'grid.power': () => form.grid.power,
    'grid.import_power': () => form.grid.importPower,
    'grid.export_power': () => form.grid.exportPower,
    'grid.energy_import': () => form.grid.energyImport,
    'grid.energy_export': () => form.grid.energyExport,
    'battery.power': () => form.battery.power,
    'battery.soc': () => form.battery.soc,
    house: () => form.house,
  };
  const target = refFields[path];
  if (target && ref) {
    Object.assign(target(), ref);
    if (path === 'grid.power') form.grid.mode = 'single';
    if (path === 'grid.import_power' || path === 'grid.export_power') form.grid.mode = 'pair';
    if (path.startsWith('battery.')) form.battery.enabled = true;
    return true;
  }
  if (path === 'battery.capacity_wh' && typeof value === 'number') {
    form.battery.capacityKwh = whToKwh(value);
    form.battery.enabled = true;
    return true;
  }
  const pv = /^pv\.(\d+)\.(name|power)$/.exec(path);
  if (pv) {
    const index = Number(pv[1]);
    if (index > 64) return false;
    while (form.pv.length <= index) form.pv.push(emptyPvRow());
    const row = form.pv[index]!;
    if (pv[2] === 'name' && typeof value === 'string') {
      row.name = value;
      return true;
    }
    if (pv[2] === 'power' && ref) {
      row.power = ref;
      return true;
    }
  }
  return false;
}

/**
 * Apply the picked suggestions. A picked `pv.N.power` brings its `pv.N.name` along (the array
 * needs a name); everything else is left alone.
 */
export function applySuggestions(
  form: SiteForm,
  suggestions: readonly Suggestion[],
  picked: ReadonlySet<string>,
): number {
  let applied = 0;
  for (const s of suggestions) {
    const nameOfPicked = /^pv\.(\d+)\.name$/.exec(s.path)
      ? picked.has(s.path.replace(/\.name$/, '.power'))
      : false;
    if (!picked.has(s.path) && !nameOfPicked) continue;
    if (applySuggestion(form, s.path, s.value)) applied += 1;
  }
  return applied;
}
