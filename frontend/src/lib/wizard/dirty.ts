// SPDX-License-Identifier: Apache-2.0
// Unsaved edits: a form is dirty when what it would store differs from what is stored. Both sides
// go through the same `fromForm` mapping, so rows nobody filled in and defaults do not count.
import type {
  BatterySettings,
  ForecastConfig,
  GoalsConfig,
  OperationConfig,
  RegionConfig,
  SiteConfig,
  TariffConfig,
} from '../api/types';
import {
  batteryFormFromConfig,
  batteryFromForm,
  type BatteryForm,
  type ModelDefaults,
} from './batteryForm';
import { forecastFormFromConfig, forecastFromForm, type ForecastForm } from './forecastForm';
import {
  goalsFormFromConfig,
  goalsFromForm,
  operationFormFromConfig,
  operationFromForm,
  type GoalsForm,
  type OperationForm,
} from './goalsForm';
import { regionFormFromConfig, regionFromForm, type RegionForm } from './regionForm';
import { siteFormFromSite, siteFromForm, type SiteForm } from './siteForm';
import { tariffFormFromConfig, tariffFromForm, type TariffForm } from './tariffForm';

export type DirtySection =
  'site' | 'region' | 'tariff' | 'forecast' | 'battery' | 'goals' | 'operation';

export interface DirtyInput {
  forms: {
    site: SiteForm;
    region: RegionForm;
    tariff: TariffForm;
    forecast: ForecastForm;
    battery: BatteryForm;
    goals: GoalsForm;
    operation: OperationForm;
  };
  saved: {
    site: SiteConfig | null;
    region: RegionConfig | null;
    tariff: TariffConfig | null;
    forecast: ForecastConfig | null;
    battery: BatterySettings | null;
    goals: GoalsConfig | null;
    operation: OperationConfig | null;
  };
  /** Site values that fill an empty model (both sides use them, so they never count as edits). */
  modelDefaults?: ModelDefaults;
  batteryUsesModbus?: boolean;
  deviceIds: readonly string[];
}

const same = (a: unknown, b: unknown): boolean => JSON.stringify(a) === JSON.stringify(b);

/** Sections whose form differs from the stored document (empty when nothing is unsaved). */
export function dirtySections({
  forms,
  saved,
  deviceIds,
  modelDefaults = {},
  batteryUsesModbus = false,
}: DirtyInput): DirtySection[] {
  const dirty: DirtySection[] = [];
  const check = (name: DirtySection, current: () => unknown, baseline: () => unknown): void => {
    try {
      if (!same(current(), baseline())) dirty.push(name);
    } catch {
      // A form that cannot be mapped yet is mid-edit: that is unsaved work.
      dirty.push(name);
    }
  };
  check(
    'site',
    () => siteFromForm(forms.site),
    () => siteFromForm(siteFormFromSite(saved.site)),
  );
  check(
    'region',
    () => regionFromForm(forms.region),
    () => regionFromForm(regionFormFromConfig(saved.region)),
  );
  check(
    'tariff',
    () => tariffFromForm(forms.tariff),
    () => tariffFromForm(tariffFormFromConfig(saved.tariff)),
  );
  check(
    'forecast',
    () => forecastFromForm(forms.forecast),
    () => forecastFromForm(forecastFormFromConfig(saved.forecast)),
  );
  check(
    'battery',
    () => batteryFromForm(forms.battery, batteryUsesModbus),
    () =>
      batteryFromForm(
        batteryFormFromConfig(saved.battery, modelDefaults),
        saved.battery?.modbus != null,
      ),
  );
  check(
    'goals',
    () => goalsFromForm(forms.goals),
    () => goalsFromForm(goalsFormFromConfig(saved.goals)),
  );
  check(
    'operation',
    () => operationFromForm(forms.operation, deviceIds),
    () => operationFromForm(operationFormFromConfig(saved.operation), deviceIds),
  );
  return dirty;
}
