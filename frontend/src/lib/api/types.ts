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
export type Reason = S['Reason'];
export type SiteConfig = S['SiteConfig'];
export type PvArray = S['PvArray'];
export type GridConfig = S['GridConfig'];
export type BatteryConfig = S['BatteryConfig'];
export type EntityRef = S['EntityRef'];
export type EntityCandidate = S['EntityCandidateOut'];
export type ValidationIssue = S['ValidationError'];

export type SnapshotMessage = S['SnapshotMessage'];
export type FlowsMessage = S['FlowsMessage'];
export type HealthMessage = S['HealthMessage'];
export type DecisionsMessage = S['DecisionsMessage'];
export type LiveMessage = SnapshotMessage | FlowsMessage | HealthMessage | DecisionsMessage;
