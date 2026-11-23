// SPDX-License-Identifier: Apache-2.0
import { describe, expect, it } from 'vitest';
import type {
  DeviceConfig,
  DeviceTemplate,
  ForecastConfig,
  RegionTemplate,
  TariffConfig,
} from '../api/types';
import {
  controlRoleOf,
  deviceFormFromConfig,
  deviceFromForm,
  newDeviceForm,
  newDeviceId,
  powerModelOptions,
  normaliseName,
  preferredPowerKind,
  seemsConfiguredAlready,
  SLOW_POWER_SENSOR,
  uniqueName,
  validateDeviceForm,
} from './deviceForm';
import {
  emptyForecastForm,
  ensureRoleFields,
  forecastFormFromConfig,
  forecastFromForm,
  setForecastTemplate,
} from './forecastForm';
import { num, slugify, uniqueId } from './forms';
import {
  goalsFormFromConfig,
  NIGHT_SCHEDULE,
  goalsFromForm,
  operationFormFromConfig,
  operationFromForm,
  TIME_PATTERN,
} from './goalsForm';
import {
  dropIndex,
  dropIndexWhileDragging,
  moveItem,
  nextPriority,
  orderedDevices,
  targetFor,
  withPriorities,
} from './priorities';
import {
  applyRegionTemplate,
  regionFormFromConfig,
  regionFromForm,
  validateRegionForm,
} from './regionForm';
import {
  addTier,
  emptyTariffForm,
  isTariffComplete,
  removeTier,
  tariffFormFromConfig,
  tariffFromForm,
  tiersFromRegion,
} from './tariffForm';

const ref = (entity_id: string, invert_sign = false) => ({ entity_id, invert_sign });

describe('primitives', () => {
  it('slugifies names into valid unique ids', () => {
    expect(slugify('Pool Pump')).toBe('pool_pump');
    expect(slugify('Klimagerät Wohnzimmer')).toBe('klimagerat_wohnzimmer');
    expect(slugify('Straße')).toBe('strasse');
    expect(slugify('3D Drucker')).toBe('d_3d_drucker');
    expect(slugify('***')).toBe('device');
    expect(slugify('x'.repeat(80))).toHaveLength(48);
    expect(/^[a-z][a-z0-9_]{0,47}$/.test(slugify('  ÄÖÜ 12 '))).toBe(true);
    const taken = new Set(['pool', 'pool_2']);
    expect(uniqueId('pool', taken)).toBe('pool_3');
    expect(uniqueId('lamp', taken)).toBe('lamp');
    expect(uniqueId('x'.repeat(48), new Set(['x'.repeat(48)])).length).toBeLessThanOrEqual(48);
  });
  it('reads number inputs defensively', () => {
    expect(num(3)).toBe(3);
    expect(num(Number.NaN)).toBeNull();
    expect(num(undefined)).toBeNull();
    expect(num('3')).toBeNull();
  });
});

describe('region form', () => {
  const cfg = {
    schema_version: 1,
    region_id: 'de',
    currency: 'EUR',
    timezone: 'Europe/Berlin',
    language: 'de' as const,
  };
  it('round-trips and normalises the currency', () => {
    expect(regionFromForm(regionFormFromConfig(cfg))).toEqual(cfg);
    const form = regionFormFromConfig(cfg);
    form.currency = ' chf ';
    expect(regionFromForm(form).currency).toBe('CHF');
  });
  it('defaults to the generic profile with the given time zone', () => {
    const form = regionFormFromConfig(null, 'Europe/Vienna');
    expect(form).toEqual({
      regionId: 'generic',
      currency: 'EUR',
      timezone: 'Europe/Vienna',
      language: 'auto',
    });
  });
  it('takes currency and time zone from a template but keeps them for the generic profile', () => {
    const form = regionFormFromConfig(null, 'Europe/Vienna');
    applyRegionTemplate(form, {
      id: 'de',
      currency: 'EUR',
      timezone: 'Europe/Berlin',
    } as RegionTemplate);
    expect(form).toMatchObject({ regionId: 'de', timezone: 'Europe/Berlin' });
    form.currency = 'CHF';
    applyRegionTemplate(form, { id: 'generic', currency: null, timezone: null } as RegionTemplate);
    expect(form).toMatchObject({ regionId: 'generic', currency: 'CHF', timezone: 'Europe/Berlin' });
  });
  it('validates', () => {
    expect(
      validateRegionForm({ regionId: '', currency: 'EU', timezone: '', language: 'auto' }),
    ).toEqual(['region', 'currency', 'timezone']);
  });
});

describe('forecast form', () => {
  const learning = { min_history_days: 30, load_ml_mode: 'shadow' as const };
  it('round-trips a stored forecast without any tariff', () => {
    const cfg: ForecastConfig = {
      schema_version: 2,
      sources: [
        {
          template_id: 'solcast.pv',
          entities: { forecast_today: ref('sensor.today') },
          weight_mode: 'manual',
          weight: 0.5,
          config_entry_id: null,
        },
      ],
      learning: { min_history_days: 14, load_ml_mode: 'off' },
      load: { workday: ref('binary_sensor.workday_sensor'), weather: null },
    };
    expect(forecastFromForm(forecastFormFromConfig(cfg))).toEqual(cfg);
    expect(forecastFromForm(forecastFormFromConfig(null))).toEqual({
      schema_version: 2,
      sources: [],
      learning,
      load: { workday: null, weather: null },
    });
  });
  it('gives each source the entities of its own roles', () => {
    const form = emptyForecastForm();
    setForecastTemplate(form, 'a.pv', true, ['today']);
    setForecastTemplate(form, 'b.pv', true, ['b_today']);
    form.entities['a.pv']!.today = ref('sensor.a');
    form.entities['b.pv']!.b_today = ref('sensor.b');
    const sources = forecastFromForm(form).sources!;
    expect(sources.map((s) => Object.keys(s.entities ?? {}))).toEqual([['today'], ['b_today']]);
  });
  it('keeps the entities of two sources that share a role name apart', () => {
    const cfg: ForecastConfig = {
      schema_version: 2,
      sources: [
        {
          template_id: 'solcast.pv',
          entities: { today: ref('sensor.solcast_today') },
          weight_mode: 'auto',
          weight: null,
        },
        {
          template_id: 'forecast_solar.pv',
          entities: { today: ref('sensor.fs_today') },
          weight_mode: 'auto',
          weight: null,
        },
      ],
      learning,
      load: { workday: null, weather: null },
    };
    const form = forecastFormFromConfig(cfg);
    expect(form.entities['solcast.pv']!.today).not.toBe(form.entities['forecast_solar.pv']!.today);
    form.entities['forecast_solar.pv']!.today = ref('sensor.fs_today_new');
    const sources = forecastFromForm(form).sources!;
    expect(sources.map((s) => s.entities?.today?.entity_id)).toEqual([
      'sensor.solcast_today',
      'sensor.fs_today_new',
    ]);
    setForecastTemplate(form, 'solcast.pv', false, ['today']);
    expect(forecastFromForm(form).sources!.map((s) => s.entities?.today?.entity_id)).toEqual([
      'sensor.fs_today_new',
    ]);
  });
  it('drops forecast entities of a template that is switched off', () => {
    const form = emptyForecastForm();
    setForecastTemplate(form, 'solcast.pv', true, ['forecast_today']);
    ensureRoleFields(form, 'solcast.pv', ['forecast_today', 'forecast_tomorrow']);
    form.entities['solcast.pv']!.forecast_today = ref('sensor.today');
    expect(Object.keys(forecastFromForm(form).sources![0]!.entities!)).toEqual(['forecast_today']);
    setForecastTemplate(form, 'solcast.pv', false, ['forecast_today', 'forecast_tomorrow']);
    expect(form.templateIds).toEqual([]);
    expect(form.entities).toEqual({});
    expect(forecastFromForm(form).sources).toEqual([]);
  });
});

describe('tariff form', () => {
  it('stores a fixed import price and no feed-in', () => {
    const form = emptyTariffForm();
    expect(isTariffComplete(form)).toBe(false);
    form.importFixed = 0.32;
    form.exportKind = 'none';
    expect(isTariffComplete(form)).toBe(true);
    expect(tariffFromForm(form)).toEqual({
      schema_version: 3,
      import: {
        kind: 'fixed',
        template_id: null,
        price_entity: null,
        fixed_price: 0.32,
        formula: null,
        tou: null,
      },
      export: { kind: 'none', fixed_price: null, tiers: [] },
    });
  });
  it('maps a dynamic tariff with template and price entity, dropping the fixed price', () => {
    const form = emptyTariffForm();
    form.importKind = 'dynamic';
    form.importFixed = 0.3;
    form.importTemplateId = 'tibber.price';
    form.priceEntity = ref('sensor.tibber');
    form.exportKind = 'fixed';
    form.exportFixed = 0.08;
    const cfg = tariffFromForm(form);
    expect(cfg.import).toEqual({
      kind: 'dynamic',
      template_id: 'tibber.price',
      price_entity: ref('sensor.tibber'),
      fixed_price: null,
      formula: null,
      tou: null,
      role_entities: {},
      action_params: {},
      series_key: null,
      price_includes: null,
    });
    expect(isTariffComplete(form)).toBe(true);
  });
  it('a dynamic sensor without template carries no template id', () => {
    const form = emptyTariffForm();
    form.importKind = 'dynamic';
    form.dynamicSource = 'entity';
    form.importTemplateId = 'tibber.price';
    form.priceEntity = ref('sensor.p');
    expect(tariffFromForm(form).import.template_id).toBeNull();
  });
  it('edits tiers: last one is unbounded, at least one stays', () => {
    const form = emptyTariffForm();
    form.importFixed = 0.3;
    form.exportKind = 'tiered';
    form.tiers = [{ upToKwp: 10, price: 0.08 }];
    addTier(form);
    expect(isTariffComplete(form)).toBe(false);
    form.tiers[1]!.price = 0.07;
    expect(isTariffComplete(form)).toBe(true);
    form.tiers[0]!.upToKwp = 10;
    form.tiers[1]!.upToKwp = 5;
    const tiers = tariffFromForm(form).export.tiers!;
    expect(tiers[0]!.up_to_kwp).toBe(10);
    expect(tiers[1]!.up_to_kwp).toBeNull();
    removeTier(form, 1);
    removeTier(form, 0);
    expect(form.tiers).toHaveLength(1);
  });
  it('round-trips a stored tariff', () => {
    const cfg: TariffConfig = {
      schema_version: 3,
      import: {
        kind: 'dynamic',
        template_id: 'tibber.price',
        price_entity: ref('sensor.t'),
        fixed_price: null,
        role_entities: {},
        action_params: {},
        series_key: null,
        price_includes: 'spot_only',
        formula: {
          spot_multiplier: 1,
          grid_fee: 0.1,
          grid_fee_windows: [],
          levies: 0.02,
          margin: 0.01,
          vat_pct: 19,
        },
        tou: null,
      },
      export: {
        kind: 'tiered',
        fixed_price: null,
        tiers: [
          { up_to_kwp: 10, price: 0.08 },
          { up_to_kwp: null, price: 0.07 },
        ],
      },
    };
    expect(tariffFromForm(tariffFormFromConfig(cfg))).toEqual(cfg);
  });
  it('carries a time-of-use tariff through unchanged', () => {
    const tou = {
      base_price: 0.3,
      periods: [{ window: { days: [0, 1, 2, 3, 4], start: '22:00', end: '06:00' }, price: 0.2 }],
    };
    const cfg: TariffConfig = {
      schema_version: 3,
      import: {
        kind: 'tou',
        template_id: null,
        price_entity: null,
        fixed_price: null,
        formula: null,
        tou,
      },
      export: { kind: 'none', fixed_price: null, tiers: [] },
    };
    const form = tariffFormFromConfig(cfg);
    expect(isTariffComplete(form)).toBe(true);
    expect(tariffFromForm(form)).toEqual(cfg);
  });
  it('offers region tiers as rows', () => {
    expect(
      tiersFromRegion({
        feed_in_tiers: [
          { up_to_kwp: 10, rate_per_kwh: 0.08 },
          { up_to_kwp: null, rate_per_kwh: 0.07 },
        ],
      } as RegionTemplate),
    ).toEqual([
      { upToKwp: 10, price: 0.08 },
      { upToKwp: null, price: 0.07 },
    ]);
    expect(tiersFromRegion(undefined)).toEqual([]);
  });
});

const TEMPLATE = {
  id: 'generic.switch',
  device_kind: 'switch',
  roles: {
    switch: {
      required: true,
      matcher: { domain: 'switch' },
      description: 'k',
      same_device_as: null,
    },
    power: {
      required: false,
      matcher: { domain: 'sensor', device_class: 'power' },
      description: 'k',
      same_device_as: 'switch',
    },
    energy: {
      required: false,
      matcher: { domain: 'sensor', device_class: 'energy' },
      description: 'k',
      same_device_as: 'switch',
    },
  },
  defaults: {
    power_model: { kind: 'nominal' },
    control: { kind: 'on_off', role: 'switch' },
    min_run_s: 300,
    min_pause_s: 120,
    settle_s: 3,
    hysteresis_w: 100,
    hysteresis_delay_s: 30,
    max_switches_per_hour: 6,
    comfort: null,
  },
} as unknown as DeviceTemplate;

describe('device form', () => {
  it('builds a form from the template defaults (minutes in the form, seconds stored)', () => {
    const form = newDeviceForm(TEMPLATE, 'Pool pump', 2);
    expect(form.minRunMin).toBe(5);
    expect(form.minPauseMin).toBe(2);
    expect(Object.keys(form.entities)).toEqual(['switch', 'power', 'energy']);
    form.entities.switch = ref('switch.pump');
    form.nominalW = 750;
    const dev = deviceFromForm(form, 'pool_pump', TEMPLATE);
    expect(dev).toMatchObject({
      id: 'pool_pump',
      template_id: 'generic.switch',
      kind: 'switch',
      priority: 2,
      min_run_s: 300,
      min_pause_s: 120,
      // protection defaults of the template are carried into the new device
      settle_s: 3,
      hysteresis_w: 100,
      hysteresis_delay_s: 30,
      max_switches_per_hour: 6,
      entities: { switch: ref('switch.pump') },
      power_model: { kind: 'nominal', nominal_w: 750, power_role: null, energy_role: null },
      control: { kind: 'on_off', role: 'switch' },
      comfort: null,
    });
  });
  it('stores null (safety default) when the template has no protection defaults', () => {
    const bare = {
      ...TEMPLATE,
      defaults: { power_model: { kind: 'nominal' }, control: TEMPLATE.defaults.control },
    } as unknown as DeviceTemplate;
    const form = newDeviceForm(bare, 'Lamp', 0);
    form.entities.switch = ref('switch.lamp');
    form.nominalW = 10;
    expect(deviceFromForm(form, 'lamp', bare)).toMatchObject({
      settle_s: null,
      hysteresis_w: null,
      hysteresis_delay_s: null,
      max_switches_per_hour: null,
    });
  });
  it('chooses the power model from the mapped roles', () => {
    const entities = { switch: ref('switch.a'), power: ref(''), energy: ref('') };
    expect(preferredPowerKind(TEMPLATE, entities)).toBe('nominal');
    expect(
      powerModelOptions(TEMPLATE, entities).find((o) => o.kind === 'measured')?.available,
    ).toBe(false);
    entities.power = ref('sensor.p');
    expect(preferredPowerKind(TEMPLATE, entities)).toBe('measured');
    entities.power = ref('');
    entities.energy = ref('sensor.e');
    expect(preferredPowerKind(TEMPLATE, entities)).toBe('counter_derived');
  });
  it('stores the roles that belong to the chosen model', () => {
    const form = newDeviceForm(TEMPLATE, 'Pump', 0);
    form.entities.switch = ref('switch.a');
    form.entities.power = ref('sensor.p', true);
    form.powerKind = 'measured';
    const dev = deviceFromForm(form, 'pump', TEMPLATE);
    expect(dev.power_model).toEqual({
      kind: 'measured',
      power_role: 'power',
      energy_role: null,
      nominal_w: null,
      counter_update_s: null,
      power_update_s: null,
    });
    expect(dev.entities?.power?.invert_sign).toBe(true);
    form.entities.energy = ref('sensor.e', true);
    form.powerKind = 'counter_derived';
    expect(deviceFromForm(form, 'pump', TEMPLATE).entities?.energy?.invert_sign).toBe(false);
  });
  it('validates required roles, nominal power and comfort', () => {
    const form = newDeviceForm(TEMPLATE, '', 0);
    let keys = validateDeviceForm(form, TEMPLATE).map((e) => e.key);
    expect(keys).toEqual(['name', 'role_missing', 'nominal_missing']);
    form.name = 'x';
    form.entities.switch = ref('switch.a');
    form.nominalW = 100;
    expect(validateDeviceForm(form, TEMPLATE)).toEqual([]);
    form.powerKind = 'measured';
    keys = validateDeviceForm(form, TEMPLATE).map((e) => e.key);
    expect(keys).toEqual(['power_model_unavailable']);
    const climate = {
      ...TEMPLATE,
      device_kind: 'climate',
      defaults: {
        ...TEMPLATE.defaults,
        control: { kind: 'setpoint', role: 'switch', shift_max_c: 2, step_c: 0.5 },
      },
    } as unknown as DeviceTemplate;
    const cf = newDeviceForm(climate, 'AC', 0);
    expect(cf.comfortMin).toBe(20);
    cf.comfortMin = null;
    cf.entities.switch = ref('switch.a');
    cf.nominalW = 1000;
    expect(validateDeviceForm(cf, climate).map((e) => e.key)).toContain('comfort_missing');
    cf.comfortMin = 26;
    cf.comfortMax = 20;
    expect(validateDeviceForm(cf, climate).map((e) => e.key)).toContain('comfort_order');
  });
  it('keeps fields the form does not edit when editing a stored device', () => {
    const stored = {
      id: 'pool',
      template_id: 'generic.switch',
      name: 'Pool',
      kind: 'switch',
      entities: { switch: ref('switch.pool') },
      power_model: { kind: 'nominal', nominal_w: 900 },
      control: { kind: 'on_off', role: 'switch' },
      priority: 3,
      min_run_s: 600,
      min_pause_s: 0,
      max_switches_per_hour: 4,
      hysteresis_w: 50,
      windows: [{ start: '08:00', end: '10:00', days: [0], mode: 'allow' }],
    } as unknown as DeviceConfig;
    const form = deviceFormFromConfig(stored, TEMPLATE);
    form.name = 'Pool pump';
    const out = deviceFromForm(form, 'pool', TEMPLATE);
    expect(out).toMatchObject({
      id: 'pool',
      name: 'Pool pump',
      max_switches_per_hour: 4,
      hysteresis_w: 50,
      priority: 3,
      min_run_s: 600,
    });
    expect(out.windows).toHaveLength(1);
  });
  it('makes ids and names unique', () => {
    expect(newDeviceId('Pool pump', new Set(['pool_pump']))).toBe('pool_pump_2');
    expect(uniqueName('Switch', ['Switch', 'switch 2'])).toBe('Switch 3');
    expect(uniqueName('Lamp', ['Switch'])).toBe('Lamp');
  });
});

const LG = {
  id: 'lg.thinq_climate',
  device_kind: 'climate',
  roles: {
    climate: {
      required: true,
      matcher: { domain: 'climate' },
      description: 'k',
      same_device_as: null,
    },
    energy_today: {
      required: true,
      matcher: { domain: 'sensor', device_class: 'energy' },
      description: 'k',
      same_device_as: 'climate',
    },
    power: {
      required: false,
      matcher: { domain: 'sensor', device_class: 'power', state_class: 'measurement' },
      description: 'k',
      same_device_as: null,
    },
  },
  defaults: {
    power_model: { kind: 'learned', energy_role: 'energy_today', counter_update_s: 3600 },
    control: { kind: 'setpoint', role: 'climate', shift_max_c: 2, step_c: 1 },
    min_run_s: 600,
    min_pause_s: 300,
  },
} as unknown as DeviceTemplate;

describe('optional power sensor of another device (climate templates)', () => {
  it('keeps the template default while the power role is unmapped', () => {
    const form = newDeviceForm(LG, 'AC', 0);
    form.entities.climate = ref('climate.ac');
    form.entities.energy_today = ref('sensor.ac_energy_today');
    expect(preferredPowerKind(LG, form.entities)).toBe('learned');
    form.entities.energy_today = ref('');
    expect(preferredPowerKind(LG, form.entities)).toBe('learned');
  });
  it('preselects measured when the power role is mapped, but stays switchable', () => {
    const form = newDeviceForm(LG, 'AC', 0);
    form.entities.climate = ref('climate.ac');
    form.entities.energy_today = ref('sensor.ac_energy_today');
    form.entities.power = ref('sensor.ac_energy_current');
    expect(preferredPowerKind(LG, form.entities)).toBe('measured');
    form.powerKind = preferredPowerKind(LG, form.entities);
    const dev = deviceFromForm(form, 'ac', LG);
    expect(dev.power_model).toEqual({
      kind: 'measured',
      power_role: 'power',
      energy_role: null,
      nominal_w: null,
      counter_update_s: null,
      power_update_s: null,
    });
    expect(dev.entities?.power?.entity_id).toBe('sensor.ac_energy_current');
    // the user keeps the learned model: the power sensor then only trains it
    form.powerKind = 'learned';
    form.nominalW = 900;
    expect(deviceFromForm(form, 'ac', LG).power_model).toMatchObject({
      kind: 'learned',
      power_role: 'power',
      energy_role: 'energy_today',
      counter_update_s: 3600,
    });
    expect(powerModelOptions(LG, form.entities).find((o) => o.kind === 'measured')?.available).toBe(
      true,
    );
  });
});

describe('Phase 3 device fields', () => {
  const climate = (): ReturnType<typeof newDeviceForm> => {
    const form = newDeviceForm(LG, 'Living room AC', 0);
    form.entities.climate = ref('climate.living_room');
    form.comfortMin = 20;
    form.comfortMax = 26;
    return form;
  };

  it('starts a new device without switch-on permission and with the next-day policy', () => {
    const dev = deviceFromForm(climate(), 'ac', LG);
    expect(dev).toMatchObject({
      allow_switch_on: false,
      switch_on_hvac_mode: null,
      override_policy: { kind: 'next_day', resume_time: '04:00:00', hours: null },
    });
  });

  it('stores allow_switch_on with its HVAC mode, and requires the mode', () => {
    const form = climate();
    form.allowSwitchOn = true;
    expect(validateDeviceForm(form, LG).map((e) => e.key)).toContain('hvac_mode_missing');
    form.switchOnHvacMode = 'cool';
    expect(validateDeviceForm(form, LG).map((e) => e.key)).not.toContain('hvac_mode_missing');
    expect(deviceFromForm(form, 'ac', LG)).toMatchObject({
      allow_switch_on: true,
      switch_on_hvac_mode: 'cool',
    });
  });

  it('drops the HVAC mode again when the permission is off or the device is no climate', () => {
    const form = climate();
    form.allowSwitchOn = true;
    form.switchOnHvacMode = 'heat';
    form.allowSwitchOn = false;
    expect(deviceFromForm(form, 'ac', LG)).toMatchObject({
      allow_switch_on: false,
      switch_on_hvac_mode: null,
    });
    const plug = newDeviceForm(TEMPLATE, 'Pump', 0);
    plug.allowSwitchOn = true;
    plug.switchOnHvacMode = 'heat';
    expect(deviceFromForm(plug, 'pump', TEMPLATE)).toMatchObject({
      allow_switch_on: false,
      switch_on_hvac_mode: null,
    });
  });

  it('builds the override policy: a time of the next day, or hours', () => {
    const form = climate();
    form.overrideResumeTime = '05:30';
    expect(deviceFromForm(form, 'ac', LG).override_policy).toEqual({
      kind: 'next_day',
      resume_time: '05:30:00',
      hours: null,
    });
    form.overrideKind = 'hours';
    form.overrideHours = 6;
    expect(deviceFromForm(form, 'ac', LG).override_policy).toEqual({
      kind: 'hours',
      resume_time: '05:30:00',
      hours: 6,
    });
  });

  it('validates hours (1-48, whole) and the resume time', () => {
    const form = climate();
    form.overrideKind = 'hours';
    for (const bad of [null, 0, 49, 2.5]) {
      form.overrideHours = bad;
      expect(
        validateDeviceForm(form, LG).map((e) => e.key),
        String(bad),
      ).toContain('override_hours');
    }
    form.overrideHours = 48;
    expect(validateDeviceForm(form, LG).map((e) => e.key)).not.toContain('override_hours');
    form.overrideKind = 'next_day';
    form.overrideResumeTime = '';
    expect(validateDeviceForm(form, LG).map((e) => e.key)).toContain('resume_time');
  });

  it('loads a stored policy and the update period of the power sensor', () => {
    const stored = {
      ...deviceFromForm(climate(), 'ac', LG),
      allow_switch_on: true,
      switch_on_hvac_mode: 'heat_cool',
      override_policy: { kind: 'hours', resume_time: '04:00:00', hours: 12 },
      power_model: { kind: 'measured', power_role: 'power', power_update_s: 300 },
      entities: { climate: ref('climate.a'), power: ref('sensor.p') },
    } as unknown as DeviceConfig;
    const form = deviceFormFromConfig(stored, LG);
    expect(form).toMatchObject({
      allowSwitchOn: true,
      switchOnHvacMode: 'heat_cool',
      overrideKind: 'hours',
      overrideHours: 12,
      overrideResumeTime: '04:00',
    });
    expect(form.powerRoles.powerUpdateS).toBe(300);
    const out = deviceFromForm(form, 'ac', LG);
    expect(out.power_model.power_update_s).toBe(300);
    expect(out.override_policy).toMatchObject({ kind: 'hours', hours: 12 });
  });

  it('stores power_update_s for the measured and learned model and validates its range', () => {
    const form = climate();
    form.entities.power = ref('sensor.ac_energy_current');
    form.powerKind = 'measured';
    form.powerRoles.powerUpdateS = 300;
    expect(deviceFromForm(form, 'ac', LG).power_model.power_update_s).toBe(300);
    form.powerRoles.powerUpdateS = 0;
    expect(validateDeviceForm(form, LG).map((e) => e.key)).toContain('power_update');
    form.powerRoles.powerUpdateS = 86_401;
    expect(validateDeviceForm(form, LG).map((e) => e.key)).toContain('power_update');
    form.powerRoles.powerUpdateS = 300;
    form.powerKind = 'learned';
    form.nominalW = 900;
    // learned also reads the power sensor: the period stays (round trip through the stored device)
    const learned = deviceFromForm(form, 'ac', LG);
    expect(learned.power_model).toMatchObject({
      kind: 'learned',
      power_role: 'power',
      power_update_s: 300,
    });
    expect(deviceFormFromConfig(learned, LG).powerRoles.powerUpdateS).toBe(300);
    form.powerKind = 'counter_derived';
    expect(deviceFromForm(form, 'ac', LG).power_model.power_update_s).toBeNull();
    form.powerKind = 'learned';
    form.entities.power = ref('');
    expect(deviceFromForm(form, 'ac', LG).power_model.power_update_s).toBeNull();
  });

  it('knows that the LG power sensor polls every 300 s', () => {
    expect(SLOW_POWER_SENSOR['lg.thinq_climate']).toBe(300);
  });
});

describe('same unit configured twice (hint)', () => {
  const lookup = new Map([
    [
      'climate.lg',
      { deviceId: 'dev_lg', deviceName: 'Schlafen Jake Klimaanlage', platform: 'lg_thinq' },
    ],
    [
      'climate.st',
      {
        deviceId: 'dev_st',
        deviceName: '  schlafen   jake KLIMAANLAGE',
        platform: 'smartthinq_sensors',
      },
    ],
    ['climate.other', { deviceId: 'dev_o', deviceName: 'Wohnzimmer', platform: 'lg_thinq' }],
    [
      'climate.lg_2',
      { deviceId: 'dev_lg', deviceName: 'Schlafen Jake Klimaanlage', platform: 'lg_thinq' },
    ],
    // same name, other registry device, SAME platform: two rooms called alike
    [
      'climate.lg_3',
      { deviceId: 'dev_lg3', deviceName: 'Schlafen Jake Klimaanlage', platform: 'lg_thinq' },
    ],
    [
      'climate.noplat',
      { deviceId: 'dev_np', deviceName: 'Schlafen Jake Klimaanlage', platform: null },
    ],
  ]);
  const stored = (id: string, entity: string, kind = 'climate') =>
    ({
      id,
      kind,
      entities: { climate: ref(entity) },
      control: { kind: 'setpoint', role: 'climate' },
    }) as unknown as DeviceConfig;
  const formFor = (entity: string, id: string | null = null) => {
    const form = newDeviceForm(LG, 'AC', 0);
    form.id = id;
    form.entities.climate = ref(entity);
    return form;
  };
  it('normalises names like the backend', () => {
    expect(normaliseName('  Living   ROOM ac ')).toBe('living room ac');
    expect(normaliseName(null)).toBe('');
  });
  it('shows the hint for the same name on another registry device', () => {
    const others = [stored('ac_lg', 'climate.lg')];
    expect(seemsConfiguredAlready(formFor('climate.st'), others, lookup)).toBe(true);
  });
  it('shows the hint for the same entity twice', () => {
    const others = [stored('ac_lg', 'climate.lg')];
    expect(seemsConfiguredAlready(formFor('climate.lg'), others, lookup)).toBe(true);
  });
  it('shows no hint for other names, the same registry device, itself or other kinds', () => {
    const others = [stored('ac_lg', 'climate.lg')];
    expect(seemsConfiguredAlready(formFor('climate.other'), others, lookup)).toBe(false);
    expect(seemsConfiguredAlready(formFor('climate.lg_2'), others, lookup)).toBe(false);
    expect(seemsConfiguredAlready(formFor('climate.lg_3'), others, lookup)).toBe(false);
    expect(seemsConfiguredAlready(formFor('climate.noplat'), others, lookup)).toBe(false);
    expect(seemsConfiguredAlready(formFor('climate.st', 'ac_lg'), others, lookup)).toBe(false);
    expect(
      seemsConfiguredAlready(formFor('climate.st'), [stored('x', 'climate.lg', 'switch')], lookup),
    ).toBe(false);
    expect(seemsConfiguredAlready(formFor('climate.st'), [], lookup)).toBe(false);
    expect(seemsConfiguredAlready(formFor(''), others, lookup)).toBe(false);
    // unknown registry data: no guess
    expect(seemsConfiguredAlready(formFor('climate.st'), others, new Map())).toBe(false);
  });
});

describe('priority reducer', () => {
  it('puts a new device after the highest priority, not at the count', () => {
    expect(nextPriority([])).toBe(0);
    expect(nextPriority([{ priority: 0 }, { priority: 1 }])).toBe(2);
    // gaps (a removed device) must not make the new one collide with an existing priority
    expect(nextPriority([{ priority: 0 }, { priority: 5 }])).toBe(6);
    expect(nextPriority([{ priority: 10_000 }])).toBe(10_000);
  });
  it('moves items and clamps', () => {
    expect(moveItem(['a', 'b', 'c'], 0, 2)).toEqual(['b', 'c', 'a']);
    expect(moveItem(['a', 'b', 'c'], 2, 0)).toEqual(['c', 'a', 'b']);
    expect(moveItem(['a', 'b', 'c'], 1, 99)).toEqual(['a', 'c', 'b']);
    expect(moveItem(['a', 'b'], 5, 0)).toEqual(['a', 'b']);
    expect(moveItem([], 0, 0)).toEqual([]);
    const original = ['a', 'b'];
    moveItem(original, 0, 1);
    expect(original).toEqual(['a', 'b']);
  });
  it('maps buttons to targets', () => {
    expect(targetFor(1, 3, 'up')).toBe(0);
    expect(targetFor(0, 3, 'up')).toBe(0);
    expect(targetFor(1, 3, 'down')).toBe(2);
    expect(targetFor(2, 3, 'down')).toBe(2);
    expect(targetFor(1, 3, 'first')).toBe(0);
    expect(targetFor(0, 3, 'last')).toBe(2);
  });
  it('finds the drop position from the pointer', () => {
    const centers = [50, 150, 250];
    expect(dropIndex(centers, 10)).toBe(0);
    expect(dropIndex(centers, 160)).toBe(2);
    expect(dropIndex(centers, 999)).toBe(2);
    // dragging row 0 down past the second row's centre
    expect(dropIndexWhileDragging(centers, 0, 120)).toBe(0);
    expect(dropIndexWhileDragging(centers, 0, 160)).toBe(1);
    expect(dropIndexWhileDragging(centers, 0, 300)).toBe(2);
    // dragging the last row up above the first row's centre
    expect(dropIndexWhileDragging(centers, 2, 10)).toBe(0);
  });
  it('writes the list order into the priority field', () => {
    const devices = [
      { id: 'a', priority: 0 },
      { id: 'b', priority: 1 },
      { id: 'c', priority: 2 },
    ] as DeviceConfig[];
    const order = moveItem(
      orderedDevices(devices).map((d) => d.id),
      2,
      0,
    );
    expect(withPriorities(devices, order).map((d) => [d.id, d.priority])).toEqual([
      ['a', 1],
      ['b', 2],
      ['c', 0],
    ]);
  });
  it('orders by priority and breaks ties by id', () => {
    const devices = [
      { id: 'b', priority: 0 },
      { id: 'a', priority: 0 },
      { id: 'c', priority: -1 },
    ] as DeviceConfig[];
    expect(orderedDevices(devices).map((d) => d.id)).toEqual(['c', 'a', 'b']);
  });
});

describe('goals and operation', () => {
  it('passes a stored custom grid-charge schedule through until the checkbox toggles', () => {
    const custom = { windows: [{ days: [5, 6], start: '00:00', end: '00:00' }] };
    const form = goalsFormFromConfig({
      schema_version: 2,
      climate_before_export: false,
      grid_charge_schedule: custom,
    });
    expect(form.gridChargeNightOnly).toBe(true);
    expect(goalsFromForm(form).grid_charge_schedule).toEqual(custom);
    form.gridChargeNightOnly = false;
    expect(goalsFromForm(form).grid_charge_schedule).toBeNull();
    const fresh = goalsFormFromConfig(goalsFromForm(form));
    fresh.gridChargeNightOnly = true;
    expect(goalsFromForm(fresh).grid_charge_schedule).toEqual(NIGHT_SCHEDULE);
  });
  it('maps goals both ways', () => {
    const form = goalsFormFromConfig(null);
    expect(form).toMatchObject({
      targetEnabled: false,
      gridChargeNightOnly: true,
      climateBeforeExport: false,
    });
    expect(goalsFromForm(form).battery_target).toBeNull();
    expect(goalsFromForm(form).grid_charge_schedule).toEqual(NIGHT_SCHEDULE);
    expect(goalsFromForm(form)).not.toHaveProperty('conservative_planning');
    form.gridChargeNightOnly = false;
    expect(goalsFromForm(form).grid_charge_schedule).toBeNull();
    expect(goalsFormFromConfig(goalsFromForm(form)).gridChargeNightOnly).toBe(false);
    form.targetEnabled = true;
    form.targetSoc = 90;
    form.targetTime = '17:30';
    const cfg = goalsFromForm(form);
    expect(cfg.battery_target).toEqual({ soc_pct: 90, time: '17:30' });
    expect(goalsFormFromConfig(cfg)).toMatchObject({
      targetEnabled: true,
      targetSoc: 90,
      targetTime: '17:30',
    });
    expect(TIME_PATTERN.test('17:30')).toBe(true);
    expect(TIME_PATTERN.test('24:00')).toBe(false);
  });
  it('stores only non-shadow modes of existing devices', () => {
    const form = operationFormFromConfig({
      schema_version: 1,
      mode: 'assisted',
      device_modes: { a: 'automatic', gone: 'automatic' },
      battery_mode: 'assisted',
    });
    form.deviceModes.b = 'shadow';
    form.deviceModes.c = 'assisted';
    expect(operationFromForm(form, ['a', 'b', 'c'])).toEqual({
      schema_version: 1,
      mode: 'assisted',
      device_modes: { a: 'automatic', c: 'assisted' },
      battery_mode: 'assisted',
    });
    expect(operationFormFromConfig(null).mode).toBe('shadow');
  });
});

describe('controlRoleOf', () => {
  it('returns the role of a single-entity control', () => {
    expect(controlRoleOf({ kind: 'on_off', role: 'switch' })).toBe('switch');
  });

  it('returns the first relay of a relays control', () => {
    expect(
      controlRoleOf({
        kind: 'relays',
        roles: ['relay_1', 'relay_2'],
        steps: [
          { relays: [], power_w: 0 },
          { relays: ['relay_1'], power_w: 2000 },
        ],
      }),
    ).toBe('relay_1');
  });
});
