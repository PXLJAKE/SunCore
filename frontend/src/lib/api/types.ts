// SPDX-License-Identifier: Apache-2.0
// Friendly aliases for the generated OpenAPI types (schema.d.ts, `npm run gen:api`).
import type { components } from './schema';

type S = components['schemas'];

export type Quality = S['FlowOut']['quality'];
export type FlowValue = S['FlowOut'];
export type Flows = S['FlowsOut'];
export type Health = S['HealthOut'];
export type Info = S['InfoOut'];
export type OperatingMode = S['OperatingMode'];
export type Decision = S['Decision'];
export type ControlState = S['ControlState'];
export type DeviceControlState = S['DeviceControlState'];
export type DecisionKind = S['DecisionKind'];
export type SiteControlState = S['SiteControlState'];
export type PowerEstimate = S['PowerEstimate'];
export type OverrideCommand = S['OverrideCommand'];
export type OverridePolicy = S['OverridePolicy'];
export type Reason = S['Reason'];
export type SiteConfig = S['SiteConfig'];
export type PvArray = S['PvArray'];
export type GridConfig = S['GridConfig'];
export type BatteryConfig = S['BatteryConfig'];
export type EntityRef = S['EntityRef'];
export type EntityCandidate = S['EntityCandidateOut'];
export type ValidationIssue = S['ValidationIssueOut'];

export type ConfigSectionName =
  'site' | 'region' | 'tariff' | 'forecast' | 'devices' | 'goals' | 'operation' | 'wizard';
export type RegionConfig = S['RegionConfig'];
export type TariffConfig = S['TariffConfig'];
export type ForecastConfig = S['ForecastConfig'];
export type DevicesConfig = S['DevicesConfig'];
export type GoalsConfig = S['GoalsConfig'];
export type OperationConfig = S['OperationConfig'];
export type WizardState = S['WizardState'];
export type DeviceConfig = S['DeviceConfig'];
export type DeviceKind = DeviceConfig['kind'];
export type ControlSpec = DeviceConfig['control'];
export type PowerModel = S['PowerModel'];
export type PowerModelKind = PowerModel['kind'];
export type ComfortBand = S['ComfortBand'];
export type ImportTariff = S['ImportTariff'];
export type ExportTariff = S['ExportTariff'];
export type ExportTier = S['ExportTier'];
export type BatteryTarget = S['BatteryTarget'];
export type DeviceTemplate = S['DeviceTemplate'];
export type TariffTemplate = S['TariffTemplate'];
export type ForecastTemplate = S['ForecastTemplate'];
export type RegionTemplate = S['RegionTemplate'];
export type RoleSpec = S['RoleSpec'];
export type Suggestion = S['SuggestionOut'];
export type ConfigHistoryEntry = S['ConfigHistoryEntryOut'];
export type ImportResult = S['ImportResultOut'];
export type HistoryRestore = S['HistoryRestoreOut'];
export type TemplateKind = 'device' | 'tariff' | 'forecast' | 'region';
export type TemplateList = S['TemplateListOut'];
export type TemplateSummary = S['TemplateSummaryOut'];
export type TemplateDetail = S['TemplateDetailOut'];
export type EnergyPrefs = S['EnergyPrefsOut'];
export type CheckResult = S['CheckOut'];
export type CheckItem = S['CheckItemOut'];
export type PreviewResult = S['PreviewOut'];
export type PreviewItem = S['PreviewItemOut'];

export type SnapshotMessage = S['SnapshotMessage'];
export type FlowsMessage = S['FlowsMessage'];
export type HealthMessage = S['HealthMessage'];
export type DecisionsMessage = S['DecisionsMessage'];
export type ControlMessage = S['ControlMessage'];
export type LiveMessage =
  SnapshotMessage | FlowsMessage | HealthMessage | DecisionsMessage | ControlMessage;
