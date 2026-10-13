// SPDX-License-Identifier: Apache-2.0
// Settings form model <-> SiteConfig, and the live preview of a picked entity.
import type { EntityCandidate, EntityRef, PvArray, SiteConfig } from './api/types';

export interface RefField {
  entity_id: string;
  invert_sign: boolean;
}
export type GridMode = 'single' | 'pair';

export interface SiteForm {
  schemaVersion: number;
  gridMode: GridMode;
  gridPower: RefField;
  gridImport: RefField;
  gridExport: RefField;
  pv: RefField[];
  batteryEnabled: boolean;
  batteryPower: RefField;
  batterySoc: RefField;
  house: RefField;
  /** The loaded document: fields the form does not edit (kwp, limits, ...) are kept on save. */
  base: SiteConfig | null;
}

export const emptyRef = (): RefField => ({ entity_id: '', invert_sign: false });

export function emptyForm(): SiteForm {
  return {
    schemaVersion: 2,
    gridMode: 'single',
    gridPower: emptyRef(),
    gridImport: emptyRef(),
    gridExport: emptyRef(),
    pv: [emptyRef()],
    batteryEnabled: false,
    batteryPower: emptyRef(),
    batterySoc: emptyRef(),
    house: emptyRef(),
    base: null,
  };
}

const fromRef = (r: EntityRef | null | undefined): RefField =>
  r ? { entity_id: r.entity_id, invert_sign: r.invert_sign ?? false } : emptyRef();

const toRef = (f: RefField): EntityRef | null => {
  const id = f.entity_id.trim();
  return id === '' ? null : { entity_id: id, invert_sign: f.invert_sign };
};

export function formFromSite(site: SiteConfig | null): SiteForm {
  if (!site) return emptyForm();
  const pair = !site.grid.power && (!!site.grid.import_power || !!site.grid.export_power);
  const pv = (site.pv ?? []).map((a) => a.power);
  return {
    schemaVersion: site.schema_version,
    gridMode: pair ? 'pair' : 'single',
    gridPower: fromRef(site.grid.power),
    gridImport: fromRef(site.grid.import_power),
    gridExport: fromRef(site.grid.export_power),
    pv: pv.length > 0 ? pv.map(fromRef) : [emptyRef()],
    batteryEnabled: !!site.battery,
    batteryPower: fromRef(site.battery?.power),
    batterySoc: fromRef(site.battery?.soc),
    house: fromRef(site.house),
    base: site,
  };
}

export type FormErrorKey =
  'settings.error.battery_incomplete' | 'settings.error.grid_pair_incomplete';

/** Client-side checks that would otherwise make `siteFromForm` drop data silently. */
export function validateForm(form: SiteForm): FormErrorKey[] {
  const errors: FormErrorKey[] = [];
  if (
    form.gridMode === 'pair' &&
    (toRef(form.gridImport) === null) !== (toRef(form.gridExport) === null)
  ) {
    errors.push('settings.error.grid_pair_incomplete');
  }
  if (
    form.batteryEnabled &&
    (toRef(form.batteryPower) === null) !== (toRef(form.batterySoc) === null)
  ) {
    errors.push('settings.error.battery_incomplete');
  }
  return errors;
}

/** Build the PUT body. Empty rows are dropped; the backend validates the rest (422). */
export function siteFromForm(form: SiteForm): SiteConfig {
  const grid =
    form.gridMode === 'single'
      ? { power: toRef(form.gridPower), import_power: null, export_power: null }
      : {
          power: null,
          import_power: toRef(form.gridImport),
          export_power: toRef(form.gridExport),
        };
  const power = toRef(form.batteryPower);
  const soc = toRef(form.batterySoc);
  const basePv = form.base?.pv ?? [];
  const pv: PvArray[] = [];
  form.pv.forEach((field, i) => {
    const ref = toRef(field);
    if (ref === null) return;
    const kept = basePv[i];
    pv.push({ ...kept, name: kept?.name ?? `PV ${i + 1}`, power: ref });
  });
  return {
    schema_version: form.schemaVersion,
    grid: { para14a: false, ...form.base?.grid, ...grid },
    pv,
    battery:
      form.batteryEnabled && power && soc
        ? { control: 'observe' as const, ...form.base?.battery, power, soc }
        : null,
    house: toRef(form.house),
  };
}

export type PreviewKind = 'power' | 'soc';
export type Preview =
  | { type: 'none' }
  | { type: 'unavailable' }
  | { type: 'power'; watts: number }
  | { type: 'percent'; percent: number }
  | { type: 'raw'; text: string };

/**
 * What the pipeline would read from the picked entity right now, with the sign switch applied.
 * Unit handling mirrors the backend: W and kW only; anything else is shown raw, never guessed.
 */
export function previewOf(
  candidate: EntityCandidate | undefined,
  invert: boolean,
  kind: PreviewKind,
): Preview {
  if (!candidate) return { type: 'none' };
  if (!candidate.available) return { type: 'unavailable' };
  const n = Number.parseFloat(candidate.state);
  if (!Number.isFinite(n)) return { type: 'unavailable' };
  const sign = invert ? -1 : 1;
  const unit = candidate.unit.trim().toLowerCase();
  if (kind === 'soc') {
    return unit === '%'
      ? { type: 'percent', percent: n }
      : { type: 'raw', text: `${n} ${candidate.unit}`.trim() };
  }
  if (unit === 'w') return { type: 'power', watts: sign * n };
  if (unit === 'kw') return { type: 'power', watts: sign * n * 1000 };
  return { type: 'raw', text: `${n} ${candidate.unit}`.trim() };
}

/** Maximum number of options rendered in the entity picker. */
export const MAX_SUGGESTIONS = 50;

const POWER_UNITS = new Set(['w', 'kw']);

/** Does the candidate belong in a picker of this kind (power: W/kW, soc: %)? */
export function matchesKind(c: EntityCandidate, kind: PreviewKind): boolean {
  const unit = c.unit.trim().toLowerCase();
  return kind === 'soc' ? unit === '%' : POWER_UNITS.has(unit);
}

export interface Suggestions {
  items: EntityCandidate[];
  /** Number of matches before the cap. */
  total: number;
}

/**
 * Candidates of the given kind whose entity id or friendly name contains every
 * whitespace-separated term of `query` (case-insensitive). Capped at `cap`.
 */
export function filterCandidates(
  candidates: readonly EntityCandidate[],
  kind: PreviewKind,
  query: string,
  cap: number = MAX_SUGGESTIONS,
): Suggestions {
  const terms = query.toLowerCase().split(/\s+/).filter(Boolean);
  const matches = candidates.filter((c) => {
    if (!matchesKind(c, kind)) return false;
    const hay = `${c.entity_id} ${c.name}`.toLowerCase();
    return terms.every((term) => hay.includes(term));
  });
  return { items: matches.slice(0, cap), total: matches.length };
}

/** The candidate whose entity id or exact friendly name equals `text` (case-insensitive). */
export function resolveCandidate(
  candidates: readonly EntityCandidate[],
  text: string,
): EntityCandidate | undefined {
  const needle = text.trim().toLowerCase();
  if (needle === '') return undefined;
  return (
    candidates.find((c) => c.entity_id.toLowerCase() === needle) ??
    candidates.find((c) => c.name.trim().toLowerCase() === needle)
  );
}
