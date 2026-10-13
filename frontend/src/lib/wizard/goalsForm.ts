// SPDX-License-Identifier: Apache-2.0
// Goals and operating-mode sections <-> forms.
import type { GoalsConfig, OperatingMode, OperationConfig } from '../api/types';

export interface GoalsForm {
  targetEnabled: boolean;
  targetSoc: number | null;
  targetTime: string;
  climateBeforeExport: boolean;
  gridChargeNightOnly: boolean;
}

export const DEFAULT_TARGET_TIME = '18:00';

export function goalsFormFromConfig(cfg: GoalsConfig | null): GoalsForm {
  return {
    targetEnabled: cfg?.battery_target != null,
    targetSoc: cfg?.battery_target?.soc_pct ?? 100,
    targetTime: cfg?.battery_target?.time ?? DEFAULT_TARGET_TIME,
    climateBeforeExport: cfg?.climate_before_export ?? false,
    gridChargeNightOnly: cfg?.grid_charge_night_only ?? true,
  };
}

export function goalsFromForm(form: GoalsForm): GoalsConfig {
  return {
    schema_version: 1,
    battery_target:
      form.targetEnabled && form.targetSoc != null
        ? { soc_pct: form.targetSoc, time: form.targetTime }
        : null,
    climate_before_export: form.climateBeforeExport,
    grid_charge_night_only: form.gridChargeNightOnly,
  };
}

export interface OperationForm {
  mode: OperatingMode;
  /** Explicit mode per device id; a missing entry is shadow (new devices always start there). */
  deviceModes: Record<string, OperatingMode>;
}

export function operationFormFromConfig(cfg: OperationConfig | null): OperationForm {
  return { mode: cfg?.mode ?? 'shadow', deviceModes: { ...(cfg?.device_modes ?? {}) } };
}

/** Only non-shadow entries of existing devices are stored (a missing entry means shadow). */
export function operationFromForm(
  form: OperationForm,
  deviceIds: readonly string[],
): OperationConfig {
  const device_modes: Record<string, OperatingMode> = {};
  for (const id of deviceIds) {
    const mode = form.deviceModes[id];
    if (mode && mode !== 'shadow') device_modes[id] = mode;
  }
  return { schema_version: 1, mode: form.mode, device_modes };
}

export const TIME_PATTERN = /^([01]\d|2[0-3]):[0-5]\d$/;
