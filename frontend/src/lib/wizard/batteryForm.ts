// SPDX-License-Identifier: Apache-2.0
// Battery section (Phase 4, ADR-0013) <-> form: adapter and its entities, the model the planner
// uses, strategy and risk level, the grid-charge policy and the self-sufficiency weight. Fields
// without a form field yet (the periodic calibration charge, `fallback_measured`) are carried
// through unchanged so that saving never drops them.
import type {
  BatterySettings,
  BatteryStrategy,
  ModbusLink,
  RiskLevel,
  WeeklySchedule,
} from '../api/types';
import { emptyRef, fromRef, toRef, type RefField } from './forms';
import { scheduleFromApi, scheduleToApi, type WindowRow } from './scheduleForm';

export interface ModbusForm {
  hub: string;
  slave: number | null;
  byteOrder: ModbusLink['byte_order'];
  heartbeatS: number | null;
  fallbackTimeoutS: number | null;
}

export interface BatteryForm {
  /** Template id of the battery adapter; `''` = observe only (SunCore never writes). */
  adapterTemplateId: string;
  /** Role of the adapter template -> entity. */
  entities: Record<string, RefField>;
  modbus: ModbusForm;
  // The model (kWh and percent in the form, Wh and a 0..1 share in the document).
  capacityKwh: number | null;
  maxChargeW: number | null;
  maxDischargeW: number | null;
  chargeEfficiencyPct: number | null;
  dischargeEfficiencyPct: number | null;
  reservePct: number | null;
  bmsDriftMarginPct: number | null;
  maxSocPct: number | null;
  degradationCostPerKwh: number | null;
  gridChargeMarginPerKwh: number | null;
  strategy: BatteryStrategy;
  risk: RiskLevel;
  /** Lower bound of the morning buffer; `null` = learnt only. */
  morningBufferPct: number | null;
  bridgeNextDay: boolean;
  gridChargeEnabled: boolean;
  gridChargeMaxPrice: number | null;
  gridChargeSchedule: WindowRow[];
  autarkyWeightPerKwh: number | null;
  // Carried through unchanged.
  calibration: BatterySettings['calibration'] | null;
  fallbackMeasured: boolean;
  /** What the measurement was taken on (adapter, modbus link, fallback timer) when loaded. */
  fallbackBasis: string;
  schemaVersion: number;
}

/** The setup a fallback measurement belongs to: another adapter, link or timer voids it. */
export function fallbackKey(f: BatteryForm): string {
  const m = f.modbus;
  return [f.adapterTemplateId, m.hub.trim(), m.slave, m.byteOrder, m.fallbackTimeoutS].join('|');
}

/** Whether the stored "fallback measured" still holds for the form as it is now. */
export const fallbackStillMeasured = (f: BatteryForm): boolean =>
  f.fallbackMeasured && fallbackKey(f) === f.fallbackBasis;

/** Site values that fill an empty model (the battery of the site section). */
export interface ModelDefaults {
  capacityKwh?: number | null;
  maxChargeW?: number | null;
  maxDischargeW?: number | null;
  minSocPct?: number | null;
  maxSocPct?: number | null;
}

const pct = (share: number): number => Math.round(share * 10000) / 100;
const share = (percent: number): number => Math.round(percent * 100) / 10000;
const round = (v: number, digits = 3): number => Number(v.toFixed(digits));

export const DEFAULT_MODBUS: ModbusForm = {
  hub: 'modbus_hub',
  slave: 71,
  byteOrder: 'little',
  heartbeatS: 20,
  fallbackTimeoutS: 60,
};

export function emptyBatteryForm(defaults: ModelDefaults = {}): BatteryForm {
  return {
    adapterTemplateId: '',
    entities: {},
    modbus: { ...DEFAULT_MODBUS },
    capacityKwh: defaults.capacityKwh ?? null,
    maxChargeW: defaults.maxChargeW ?? null,
    maxDischargeW: defaults.maxDischargeW ?? null,
    chargeEfficiencyPct: 95,
    dischargeEfficiencyPct: 95,
    reservePct: defaults.minSocPct ?? 10,
    bmsDriftMarginPct: 5,
    maxSocPct: defaults.maxSocPct ?? 100,
    degradationCostPerKwh: 0.04,
    gridChargeMarginPerKwh: 0.03,
    strategy: 'self_consumption',
    risk: 'cautious',
    morningBufferPct: null,
    bridgeNextDay: true,
    gridChargeEnabled: false,
    gridChargeMaxPrice: null,
    gridChargeSchedule: [],
    autarkyWeightPerKwh: 0.01,
    calibration: null,
    fallbackMeasured: false,
    fallbackBasis: '',
    schemaVersion: 1,
  };
}

export function batteryFormFromConfig(
  cfg: BatterySettings | null,
  defaults: ModelDefaults = {},
): BatteryForm {
  const form = emptyBatteryForm(defaults);
  if (!cfg) return form;
  form.adapterTemplateId = cfg.adapter_template_id ?? '';
  form.entities = Object.fromEntries(
    Object.entries(cfg.entities ?? {}).map(([role, ref]) => [role, fromRef(ref)]),
  );
  const m = cfg.model;
  if (m) {
    form.capacityKwh = round(m.capacity_wh / 1000);
    form.maxChargeW = m.max_charge_w;
    form.maxDischargeW = m.max_discharge_w;
    form.chargeEfficiencyPct = pct(m.charge_efficiency ?? 0.95);
    form.dischargeEfficiencyPct = pct(m.discharge_efficiency ?? 0.95);
    form.reservePct = m.reserve_pct ?? 10;
    form.bmsDriftMarginPct = m.bms_drift_margin_pct ?? 5;
    form.maxSocPct = m.max_soc_pct ?? 100;
    form.degradationCostPerKwh = m.degradation_cost_per_kwh ?? 0.04;
    form.gridChargeMarginPerKwh = m.grid_charge_margin_per_kwh ?? 0.03;
  }
  form.strategy = cfg.strategy ?? 'self_consumption';
  form.risk = cfg.risk ?? 'cautious';
  form.morningBufferPct = cfg.morning_buffer_pct ?? null;
  form.bridgeNextDay = cfg.bridge_next_day ?? true;
  form.gridChargeEnabled = cfg.grid_charge?.enabled ?? false;
  form.gridChargeMaxPrice = cfg.grid_charge?.max_price ?? null;
  form.gridChargeSchedule = scheduleFromApi(cfg.grid_charge?.schedule);
  form.autarkyWeightPerKwh = cfg.autarky_weight_per_kwh ?? 0.01;
  form.calibration = cfg.calibration ?? null;
  form.fallbackMeasured = cfg.fallback_measured ?? false;
  form.schemaVersion = cfg.schema_version ?? 1;
  if (cfg.modbus) {
    form.modbus = {
      hub: cfg.modbus.hub ?? 'modbus_hub',
      slave: cfg.modbus.slave,
      byteOrder: cfg.modbus.byte_order,
      heartbeatS: cfg.modbus.heartbeat_s ?? 20,
      fallbackTimeoutS: cfg.modbus.fallback_timeout_s ?? 60,
    };
  }
  form.fallbackBasis = fallbackKey(form);
  return form;
}

/** Capacity and both power limits are what the planner needs; without them there is no model. */
export const hasModel = (f: BatteryForm): boolean =>
  f.capacityKwh != null &&
  f.capacityKwh > 0 &&
  f.maxChargeW != null &&
  f.maxChargeW > 0 &&
  f.maxDischargeW != null &&
  f.maxDischargeW > 0;

export type BatteryFormError =
  | 'grid_charge_needs_rule'
  | 'adapter_entities_missing'
  | 'modbus_incomplete'
  | 'heartbeat_too_slow';

/** Problems the step reports itself (the server validates the rest). */
export function validateBatteryForm(f: BatteryForm, usesModbus: boolean): BatteryFormError[] {
  const errors: BatteryFormError[] = [];
  if (f.gridChargeEnabled && f.gridChargeMaxPrice == null && f.gridChargeSchedule.length === 0) {
    errors.push('grid_charge_needs_rule');
  }
  if (usesModbus) {
    const m = f.modbus;
    if (
      m.hub.trim() === '' ||
      m.slave == null ||
      m.heartbeatS == null ||
      m.fallbackTimeoutS == null
    ) {
      errors.push('modbus_incomplete');
    } else if (m.heartbeatS * 3 > m.fallbackTimeoutS) {
      errors.push('heartbeat_too_slow');
    }
  }
  return errors;
}

/**
 * The document to store. `usesModbus` says whether the chosen adapter's driver is
 * `kostal_modbus` (the form keeps the link fields around while the user switches adapters).
 */
export function batteryFromForm(form: BatteryForm, usesModbus: boolean): BatterySettings {
  const entities: NonNullable<BatterySettings['entities']> = {};
  if (form.adapterTemplateId !== '') {
    for (const [role, field] of Object.entries(form.entities)) {
      const ref = toRef(field, false);
      if (ref) entities[role] = ref;
    }
  }
  const schedule: WeeklySchedule | null = scheduleToApi(form.gridChargeSchedule);
  return {
    schema_version: form.schemaVersion,
    adapter_template_id: form.adapterTemplateId === '' ? null : form.adapterTemplateId,
    entities,
    model: hasModel(form)
      ? {
          capacity_wh: Math.round(form.capacityKwh! * 1000),
          max_charge_w: form.maxChargeW!,
          max_discharge_w: form.maxDischargeW!,
          charge_efficiency: share(form.chargeEfficiencyPct ?? 95),
          discharge_efficiency: share(form.dischargeEfficiencyPct ?? 95),
          reserve_pct: form.reservePct ?? 10,
          bms_drift_margin_pct: form.bmsDriftMarginPct ?? 5,
          max_soc_pct: form.maxSocPct ?? 100,
          degradation_cost_per_kwh: form.degradationCostPerKwh ?? 0.04,
          grid_charge_margin_per_kwh: form.gridChargeMarginPerKwh ?? 0.03,
        }
      : null,
    strategy: form.strategy,
    grid_charge: {
      enabled: form.gridChargeEnabled,
      max_price: form.gridChargeMaxPrice,
      schedule,
    },
    calibration: form.calibration ?? { enabled: false, interval_days: 30, target_soc_pct: 100 },
    risk: form.risk,
    morning_buffer_pct: form.morningBufferPct,
    bridge_next_day: form.bridgeNextDay,
    autarky_weight_per_kwh: form.autarkyWeightPerKwh ?? 0.01,
    modbus:
      usesModbus && form.adapterTemplateId !== ''
        ? {
            hub: form.modbus.hub.trim(),
            slave: form.modbus.slave ?? 71,
            byte_order: form.modbus.byteOrder,
            heartbeat_s: form.modbus.heartbeatS ?? 20,
            fallback_timeout_s: form.modbus.fallbackTimeoutS ?? 60,
          }
        : null,
    fallback_measured: fallbackStillMeasured(form),
  };
}

export { emptyRef };
