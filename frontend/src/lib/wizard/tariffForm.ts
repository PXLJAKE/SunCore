// SPDX-License-Identifier: Apache-2.0
// Tariff section <-> form: import price (fixed or dynamic), feed-in (fixed, tiered, none) and the
// forecast sources (templates plus the entities of their roles).
import type { ExportTier, RegionTemplate, TariffConfig } from '../api/types';
import { emptyRef, fromRef, toRef, type RefField } from './forms';

export type ImportKind = 'fixed' | 'dynamic';
export type DynamicSource = 'template' | 'entity';
export type ExportKind = 'fixed' | 'tiered' | 'none';

export interface TierRow {
  /** Upper bound in kWp; `null` = no bound (only the last tier). */
  upToKwp: number | null;
  price: number | null;
}

export interface TariffForm {
  importKind: ImportKind;
  importFixed: number | null;
  dynamicSource: DynamicSource;
  importTemplateId: string;
  priceEntity: RefField;
  exportKind: ExportKind;
  exportFixed: number | null;
  tiers: TierRow[];
  forecastTemplateIds: string[];
  /** Forecast role -> entity; rows without an entity are not sent. */
  forecastEntities: Record<string, RefField>;
}

export function emptyTariffForm(): TariffForm {
  return {
    importKind: 'fixed',
    importFixed: null,
    dynamicSource: 'template',
    importTemplateId: '',
    priceEntity: emptyRef(),
    exportKind: 'fixed',
    exportFixed: null,
    tiers: [{ upToKwp: null, price: null }],
    forecastTemplateIds: [],
    forecastEntities: {},
  };
}

export function tariffFormFromConfig(cfg: TariffConfig | null): TariffForm {
  if (!cfg) return emptyTariffForm();
  const imp = cfg.import;
  const exp = cfg.export;
  return {
    importKind: imp.kind,
    importFixed: imp.fixed_price ?? null,
    dynamicSource: imp.template_id ? 'template' : 'entity',
    importTemplateId: imp.template_id ?? '',
    priceEntity: fromRef(imp.price_entity),
    exportKind: exp.kind,
    exportFixed: exp.fixed_price ?? null,
    tiers:
      exp.tiers && exp.tiers.length > 0
        ? exp.tiers.map((t) => ({ upToKwp: t.up_to_kwp ?? null, price: t.price }))
        : [{ upToKwp: null, price: null }],
    forecastTemplateIds: [...(cfg.forecast_template_ids ?? [])],
    forecastEntities: Object.fromEntries(
      Object.entries(cfg.forecast_entities ?? {}).map(([role, ref]) => [role, fromRef(ref)]),
    ),
  };
}

/** Enough to store the tariff section (the backend requires import and export). */
export function isTariffComplete(form: TariffForm): boolean {
  const importOk =
    form.importKind === 'fixed'
      ? form.importFixed != null
      : form.dynamicSource === 'template'
        ? form.importTemplateId !== ''
        : toRef(form.priceEntity) !== null;
  const exportOk =
    form.exportKind === 'none' ||
    (form.exportKind === 'fixed' && form.exportFixed != null) ||
    (form.exportKind === 'tiered' &&
      form.tiers.length > 0 &&
      form.tiers.every((t) => t.price != null));
  return importOk && exportOk;
}

export function tariffFromForm(form: TariffForm): TariffConfig {
  const dynamic = form.importKind === 'dynamic';
  const byTemplate = dynamic && form.dynamicSource === 'template';
  const tiers: ExportTier[] =
    form.exportKind === 'tiered'
      ? form.tiers.map((t, i) => ({
          // Only the last tier may be unbounded.
          up_to_kwp: i === form.tiers.length - 1 ? null : t.upToKwp,
          price: t.price ?? 0,
        }))
      : [];
  const forecastEntities: TariffConfig['forecast_entities'] = {};
  for (const [role, field] of Object.entries(form.forecastEntities)) {
    const ref = toRef(field);
    if (ref) forecastEntities[role] = ref;
  }
  return {
    schema_version: 1,
    import: {
      kind: form.importKind,
      template_id: byTemplate ? form.importTemplateId : null,
      price_entity: dynamic ? toRef(form.priceEntity) : null,
      fixed_price: dynamic ? null : form.importFixed,
    },
    export: {
      kind: form.exportKind,
      fixed_price: form.exportKind === 'fixed' ? form.exportFixed : null,
      tiers,
    },
    forecast_template_ids: [...form.forecastTemplateIds],
    forecast_entities: forecastEntities,
  };
}

export function addTier(form: TariffForm): void {
  // The previous last tier gets a bound the user fills in; the new one is unbounded.
  form.tiers.push({ upToKwp: null, price: null });
}

export function removeTier(form: TariffForm, index: number): void {
  if (form.tiers.length > 1) form.tiers.splice(index, 1);
}

/** Tiers of a region template (data only), as form rows; `[]` when the template has none. */
export function tiersFromRegion(template: RegionTemplate | undefined): TierRow[] {
  return (template?.feed_in_tiers ?? []).map((t) => ({
    upToKwp: t.up_to_kwp ?? null,
    price: t.rate_per_kwh,
  }));
}

/** Toggle a forecast template; entities of its roles are dropped when it is switched off. */
export function setForecastTemplate(
  form: TariffForm,
  id: string,
  on: boolean,
  roles: readonly string[],
): void {
  const has = form.forecastTemplateIds.includes(id);
  if (on && !has) form.forecastTemplateIds.push(id);
  if (!on && has) {
    form.forecastTemplateIds = form.forecastTemplateIds.filter((x) => x !== id);
    for (const role of roles) delete form.forecastEntities[role];
  }
}
