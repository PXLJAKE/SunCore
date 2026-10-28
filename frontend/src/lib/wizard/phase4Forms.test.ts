// SPDX-License-Identifier: Apache-2.0
// Phase 4 wizard forms: every new config field survives a round trip (no data loss).
import { describe, expect, it } from 'vitest';
import type { BatterySettings, ForecastConfig, TariffConfig } from '../api/types';
import {
  batteryFormFromConfig,
  batteryFromForm,
  emptyBatteryForm,
  hasModel,
  validateBatteryForm,
} from './batteryForm';
import { dirtySections } from './dirty';
import { forecastFormFromConfig, forecastFromForm } from './forecastForm';
import { emptyWindow, isWindowValid, toggleDay, toggleMonth } from './scheduleForm';
import { regionFormFromConfig } from './regionForm';
import { emptySiteForm } from './siteForm';
import { goalsFormFromConfig, operationFormFromConfig } from './goalsForm';
import {
  emptyTariffForm,
  formulaApplies,
  tariffFormFromConfig,
  tariffFromForm,
} from './tariffForm';
import { forecastFormFromConfig as ffc } from './forecastForm';

const ref = (entity_id: string) => ({ entity_id, invert_sign: false });

const FULL_BATTERY: BatterySettings = {
  schema_version: 1,
  adapter_template_id: 'kostal.plenticore_g3_modbus',
  entities: { charge_limit: ref('sensor.k_1280'), external_control: ref('sensor.k_1080') },
  model: {
    capacity_wh: 11500,
    max_charge_w: 4800,
    max_discharge_w: 5200,
    charge_efficiency: 0.96,
    discharge_efficiency: 0.94,
    reserve_pct: 12,
    bms_drift_margin_pct: 4,
    max_soc_pct: 95,
    degradation_cost_per_kwh: 0.05,
    grid_charge_margin_per_kwh: 0.02,
  },
  strategy: 'target_by_time',
  grid_charge: {
    enabled: true,
    max_price: 0.12,
    schedule: { windows: [{ days: [0, 1, 2, 3, 4], start: '22:00', end: '06:00' }] },
  },
  calibration: { enabled: true, interval_days: 14, target_soc_pct: 100 },
  risk: 'balanced',
  morning_buffer_pct: 30,
  bridge_next_day: false,
  autarky_weight_per_kwh: 0.02,
  modbus: { hub: 'kostal', slave: 71, byte_order: 'big', heartbeat_s: 15, fallback_timeout_s: 60 },
  fallback_measured: true,
};

describe('battery form', () => {
  it('round-trips every field of a full battery section', () => {
    const form = batteryFormFromConfig(FULL_BATTERY);
    expect(batteryFromForm(form, true)).toEqual(FULL_BATTERY);
  });
  it('round-trips an observe-only section and drops stale entities and modbus', () => {
    const observe: BatterySettings = {
      ...FULL_BATTERY,
      adapter_template_id: null,
      entities: {},
      modbus: null,
      fallback_measured: false,
      grid_charge: { enabled: false, max_price: null, schedule: null },
    };
    const form = batteryFormFromConfig(FULL_BATTERY);
    form.adapterTemplateId = '';
    form.fallbackMeasured = false;
    form.gridChargeEnabled = false;
    form.gridChargeMaxPrice = null;
    form.gridChargeSchedule = [];
    expect(batteryFromForm(form, false)).toEqual(observe);
  });
  it('starts from the site battery when nothing is stored', () => {
    const form = batteryFormFromConfig(null, {
      capacityKwh: 10,
      maxChargeW: 5000,
      maxDischargeW: 4000,
      minSocPct: 8,
      maxSocPct: 98,
    });
    const doc = batteryFromForm(form, false);
    expect(doc.model).toMatchObject({
      capacity_wh: 10000,
      max_charge_w: 5000,
      max_discharge_w: 4000,
      reserve_pct: 8,
      max_soc_pct: 98,
    });
    expect(doc.strategy).toBe('self_consumption');
    expect(doc.risk).toBe('cautious');
    expect(doc.grid_charge).toEqual({ enabled: false, max_price: null, schedule: null });
    expect(doc.adapter_template_id).toBeNull();
  });
  it('has no model without capacity and both power limits', () => {
    const form = emptyBatteryForm();
    expect(hasModel(form)).toBe(false);
    expect(batteryFromForm(form, false).model).toBeNull();
    form.capacityKwh = 5;
    form.maxChargeW = 2000;
    form.maxDischargeW = 2000;
    expect(hasModel(form)).toBe(true);
  });
  it('reports a grid-charge policy without a rule and a slow heartbeat', () => {
    const form = emptyBatteryForm();
    form.gridChargeEnabled = true;
    expect(validateBatteryForm(form, false)).toEqual(['grid_charge_needs_rule']);
    form.gridChargeMaxPrice = 0.1;
    expect(validateBatteryForm(form, false)).toEqual([]);
    form.modbus.heartbeatS = 30;
    form.modbus.fallbackTimeoutS = 60;
    expect(validateBatteryForm(form, true)).toEqual(['heartbeat_too_slow']);
    form.modbus.hub = ' ';
    expect(validateBatteryForm(form, true)).toEqual(['modbus_incomplete']);
  });
});

describe('forecast form (phase 4 fields)', () => {
  const cfg: ForecastConfig = {
    schema_version: 2,
    sources: [
      {
        template_id: 'forecast_solar.pv',
        entities: {},
        weight_mode: 'manual',
        weight: 0.4,
        config_entry_id: 'entry_a',
      },
      {
        template_id: 'solcast.pv',
        entities: { forecast_today: ref('sensor.today') },
        weight_mode: 'auto',
        weight: null,
        config_entry_id: null,
      },
    ],
    learning: { min_history_days: 30, load_ml_mode: 'shadow' },
    load: { workday: ref('binary_sensor.workday'), weather: ref('weather.home') },
  };
  it('keeps the config entry, weights and the load inputs', () => {
    expect(forecastFromForm(ffc(cfg))).toEqual(cfg);
  });
  it('an unset manual weight falls back to automatic instead of an invalid document', () => {
    const form = forecastFormFromConfig(cfg);
    form.weights['solcast.pv'] = { weight_mode: 'manual', weight: null };
    const src = forecastFromForm(form).sources!.find((s) => s.template_id === 'solcast.pv')!;
    expect(src.weight_mode).toBe('auto');
    expect(src.weight).toBeNull();
  });
});

describe('tariff form (phase 4 fields)', () => {
  it('round-trips a time-of-use tariff with months and midnight windows', () => {
    const cfg: TariffConfig = {
      schema_version: 3,
      import: {
        kind: 'tou',
        template_id: null,
        price_entity: null,
        fixed_price: null,
        formula: null,
        tou: {
          base_price: 0.32,
          periods: [
            { window: { days: [0, 1, 2, 3, 4, 5, 6], start: '22:00', end: '06:00' }, price: 0.2 },
            {
              window: { days: [5, 6], start: '00:00', end: '00:00' },
              price: 0.22,
              months: [1, 2, 11, 12],
            },
          ],
        },
      },
      export: { kind: 'none', fixed_price: null, tiers: [] },
    };
    expect(tariffFromForm(tariffFormFromConfig(cfg))).toEqual(cfg);
  });
  it('round-trips a formula with time-variable grid fees and the source settings', () => {
    const cfg: TariffConfig = {
      schema_version: 3,
      import: {
        kind: 'dynamic',
        template_id: 'octopus_energy.rates',
        price_entity: ref('sensor.current'),
        fixed_price: null,
        role_entities: { next_day_rates: ref('event.next') },
        action_params: { areas: 'DE', currency: 'EUR' },
        series_key: 'DE',
        price_includes: 'spot_only',
        formula: {
          spot_multiplier: 1.05,
          grid_fee: 0.08,
          levies: 0.03,
          margin: 0.015,
          vat_pct: 19,
          grid_fee_windows: [
            {
              window: { days: [0, 1, 2, 3, 4], start: '17:00', end: '20:00' },
              fee: 0.12,
              months: [1, 2, 3, 10, 11, 12],
            },
          ],
        },
        tou: null,
      },
      export: { kind: 'fixed', fixed_price: 0.08, tiers: [] },
    };
    expect(tariffFromForm(tariffFormFromConfig(cfg))).toEqual(cfg);
  });
  it('stores the formula only for a dynamic source whose switch is on', () => {
    const form = emptyTariffForm();
    form.importKind = 'dynamic';
    form.dynamicSource = 'entity';
    form.priceEntity = ref('sensor.spot');
    form.formulaEnabled = false;
    expect(tariffFromForm(form).import.formula).toBeNull();
    form.formulaEnabled = true;
    form.formula.vatPct = 19;
    expect(tariffFromForm(form).import.formula?.vat_pct).toBe(19);
    form.importKind = 'fixed';
    form.importFixed = 0.3;
    expect(tariffFromForm(form).import.formula).toBeNull();
  });
  it('a formula applies to bare spot prices only', () => {
    const form = emptyTariffForm();
    form.importKind = 'dynamic';
    expect(formulaApplies(form, 'spot_only')).toBe(true);
    expect(formulaApplies(form, 'all_in')).toBe(false);
    expect(formulaApplies(form, null)).toBe(false);
    form.priceIncludes = 'spot_only';
    expect(formulaApplies(form, 'all_in')).toBe(true);
    form.priceIncludes = 'all_in';
    expect(formulaApplies(form, 'spot_only')).toBe(false);
    form.importKind = 'tou';
    expect(formulaApplies(form, 'spot_only')).toBe(false);
  });
  it('needs a base price and a period for an HT/NT tariff', () => {
    const form = emptyTariffForm();
    form.importKind = 'tou';
    form.exportKind = 'none';
    expect(tariffFormFromConfig(null).tou.periods).toEqual([]);
    form.tou.basePrice = 0.3;
    form.tou.periods.push({ window: emptyWindow(), price: 0.2, months: [] });
    expect(tariffFromForm(form).import.tou?.periods).toHaveLength(1);
  });
});

describe('schedule rows', () => {
  it('toggles days and months in order and validates windows', () => {
    const w = emptyWindow();
    toggleDay(w, 3, false);
    expect(w.days).toEqual([0, 1, 2, 4, 5, 6]);
    toggleDay(w, 3, true);
    expect(w.days).toEqual([0, 1, 2, 3, 4, 5, 6]);
    expect(isWindowValid(w)).toBe(true);
    w.days = [];
    expect(isWindowValid(w)).toBe(false);
    w.days = [1];
    w.start = '25:00';
    expect(isWindowValid(w)).toBe(false);
    expect(toggleMonth([3, 1], 2, true)).toEqual([1, 2, 3]);
    expect(toggleMonth([1, 2], 1, false)).toEqual([2]);
  });
});

describe('dirty sections with the battery', () => {
  const base = () => ({
    forms: {
      site: emptySiteForm(),
      region: regionFormFromConfig(null),
      tariff: emptyTariffForm(),
      forecast: forecastFormFromConfig(null),
      battery: batteryFormFromConfig(FULL_BATTERY),
      goals: goalsFormFromConfig(null),
      operation: operationFormFromConfig(null),
    },
    saved: {
      site: null,
      region: null,
      tariff: null,
      forecast: null,
      battery: FULL_BATTERY,
      goals: null,
      operation: null,
    },
    deviceIds: [],
    batteryUsesModbus: true,
  });
  it('is clean right after loading and dirty after an edit', () => {
    const input = base();
    expect(dirtySections(input)).not.toContain('battery');
    input.forms.battery.risk = 'bold';
    expect(dirtySections(input)).toContain('battery');
  });
});
