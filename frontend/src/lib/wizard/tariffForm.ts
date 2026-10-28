// SPDX-License-Identifier: Apache-2.0
// Tariff section (v3) <-> form: import price (fixed, dynamic with an optional price formula, or
// fixed time-of-use HT/NT) and feed-in (fixed, tiered, none). Source settings the form has no
// field for (`action_params`, `series_key`) are carried through unchanged.
import type { ExportTier, ImportTariff, RegionTemplate, TariffConfig } from '../api/types';
import { emptyRef, fromRef, toRef, type RefField } from './forms';
import {
  feesFromApi,
  feesToApi,
  periodsFromApi,
  periodsToApi,
  type FeeRow,
  type PeriodRow,
} from './scheduleForm';

export type ImportKind = 'fixed' | 'dynamic' | 'tou';
export type DynamicSource = 'template' | 'entity';
export type ExportKind = 'fixed' | 'tiered' | 'none';
export type PriceIncludes = 'spot_only' | 'all_in';

export interface TierRow {
  /** Upper bound in kWp; `null` = no bound (only the last tier). */
  upToKwp: number | null;
  price: number | null;
}

/** Spot price -> end price: `(spot x multiplier + grid fee + levies + margin) x (1 + VAT)`. */
export interface FormulaForm {
  spotMultiplier: number | null;
  gridFee: number | null;
  levies: number | null;
  margin: number | null;
  vatPct: number | null;
  feeWindows: FeeRow[];
}

export interface TouForm {
  basePrice: number | null;
  periods: PeriodRow[];
}

export interface TariffForm {
  importKind: ImportKind;
  importFixed: number | null;
  dynamicSource: DynamicSource;
  importTemplateId: string;
  priceEntity: RefField;
  /** Entities of the template's roles besides the price (Octopus' two rate sensors). */
  roleEntities: Record<string, RefField>;
  /** What the price contains, when the user states it; `null` = what the template says. */
  priceIncludes: PriceIncludes | null;
  /** The price formula on top of a spot-only price (on = the formula fields are stored). */
  formulaEnabled: boolean;
  formula: FormulaForm;
  tou: TouForm;
  exportKind: ExportKind;
  exportFixed: number | null;
  tiers: TierRow[];
  /** Carried through unchanged (no form fields yet). */
  actionParams: ImportTariff['action_params'];
  seriesKey: ImportTariff['series_key'];
}

export const emptyFormula = (): FormulaForm => ({
  spotMultiplier: 1,
  gridFee: 0,
  levies: 0,
  margin: 0,
  vatPct: 0,
  feeWindows: [],
});

export const emptyTou = (): TouForm => ({ basePrice: null, periods: [] });

export function emptyTariffForm(): TariffForm {
  return {
    importKind: 'fixed',
    importFixed: null,
    dynamicSource: 'template',
    importTemplateId: '',
    priceEntity: emptyRef(),
    roleEntities: {},
    priceIncludes: null,
    formulaEnabled: false,
    formula: emptyFormula(),
    tou: emptyTou(),
    exportKind: 'fixed',
    exportFixed: null,
    tiers: [{ upToKwp: null, price: null }],
    actionParams: {},
    seriesKey: null,
  };
}

export function tariffFormFromConfig(cfg: TariffConfig | null): TariffForm {
  if (!cfg) return emptyTariffForm();
  const imp = cfg.import;
  const exp = cfg.export;
  const f = imp.formula;
  return {
    importKind: imp.kind,
    importFixed: imp.fixed_price ?? null,
    dynamicSource: imp.template_id ? 'template' : 'entity',
    importTemplateId: imp.template_id ?? '',
    priceEntity: fromRef(imp.price_entity),
    roleEntities: Object.fromEntries(
      Object.entries(imp.role_entities ?? {}).map(([role, ref]) => [role, fromRef(ref)]),
    ),
    priceIncludes: imp.price_includes ?? null,
    formulaEnabled: f != null,
    formula: f
      ? {
          spotMultiplier: f.spot_multiplier ?? 1,
          gridFee: f.grid_fee ?? 0,
          levies: f.levies ?? 0,
          margin: f.margin ?? 0,
          vatPct: f.vat_pct ?? 0,
          feeWindows: feesFromApi(f.grid_fee_windows),
        }
      : emptyFormula(),
    tou: imp.tou
      ? { basePrice: imp.tou.base_price, periods: periodsFromApi(imp.tou.periods) }
      : emptyTou(),
    exportKind: exp.kind,
    exportFixed: exp.fixed_price ?? null,
    tiers:
      exp.tiers && exp.tiers.length > 0
        ? exp.tiers.map((t) => ({ upToKwp: t.up_to_kwp ?? null, price: t.price }))
        : [{ upToKwp: null, price: null }],
    actionParams: { ...(imp.action_params ?? {}) },
    seriesKey: imp.series_key ?? null,
  };
}

/** Enough to store the tariff section (the backend requires import and export). */
export function isTariffComplete(form: TariffForm): boolean {
  const importOk =
    form.importKind === 'tou'
      ? form.tou.basePrice != null && form.tou.periods.length > 0
      : form.importKind === 'fixed'
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
  const roleEntities: NonNullable<ImportTariff['role_entities']> = {};
  if (dynamic) {
    for (const [role, field] of Object.entries(form.roleEntities)) {
      const ref = toRef(field, false);
      if (ref) roleEntities[role] = ref;
    }
  }
  const f = form.formula;
  const imp: ImportTariff = {
    kind: form.importKind,
    template_id: byTemplate ? form.importTemplateId : null,
    price_entity: dynamic ? toRef(form.priceEntity) : null,
    fixed_price: form.importKind === 'fixed' ? form.importFixed : null,
    formula:
      dynamic && form.formulaEnabled
        ? {
            spot_multiplier: f.spotMultiplier ?? 1,
            grid_fee: f.gridFee ?? 0,
            levies: f.levies ?? 0,
            margin: f.margin ?? 0,
            vat_pct: f.vatPct ?? 0,
            grid_fee_windows: feesToApi(f.feeWindows),
          }
        : null,
    tou:
      form.importKind === 'tou'
        ? { base_price: form.tou.basePrice ?? 0, periods: periodsToApi(form.tou.periods) }
        : null,
  };
  if (dynamic) {
    imp.role_entities = roleEntities;
    imp.action_params = { ...form.actionParams };
    imp.series_key = form.seriesKey;
    imp.price_includes = form.priceIncludes;
  }
  return {
    schema_version: 3,
    import: imp,
    export: {
      kind: form.exportKind,
      fixed_price: form.exportKind === 'fixed' ? form.exportFixed : null,
      tiers,
    },
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

/**
 * Whether a price formula applies: the source delivers a bare spot price (`spot_only`), as the
 * template says or as the user stated it.
 */
export function formulaApplies(form: TariffForm, templateIncludes: PriceIncludes | null): boolean {
  if (form.importKind !== 'dynamic') return false;
  return (form.priceIncludes ?? templateIncludes) === 'spot_only';
}
