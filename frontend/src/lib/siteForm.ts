// SPDX-License-Identifier: Apache-2.0
// Settings form model <-> SiteConfig, and the live preview of a picked entity.
import type { EntityCandidate, EntityRef, SiteConfig } from './api/types';

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
}

export const emptyRef = (): RefField => ({ entity_id: '', invert_sign: false });

export function emptyForm(): SiteForm {
  return {
    schemaVersion: 1,
    gridMode: 'single',
    gridPower: emptyRef(),
    gridImport: emptyRef(),
    gridExport: emptyRef(),
    pv: [emptyRef()],
    batteryEnabled: false,
    batteryPower: emptyRef(),
    batterySoc: emptyRef(),
    house: emptyRef(),
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
  const pv = site.pv ?? [];
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
  };
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
  return {
    schema_version: form.schemaVersion,
    grid,
    pv: form.pv.map(toRef).filter((r): r is EntityRef => r !== null),
    battery: form.batteryEnabled && power && soc ? { power, soc } : null,
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
