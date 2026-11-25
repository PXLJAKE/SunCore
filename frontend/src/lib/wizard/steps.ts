// SPDX-License-Identifier: Apache-2.0
// The setup steps in order (BRIEF 4.10). The ids are stored in `wizard.completed_steps` and used
// in the URL (`#/setup/<id>`), so they never change; the numbers in the UI are positions.
import type { MessageKey } from '../i18n/index.svelte';

export const STEP_IDS = [
  'region',
  'energy',
  'grid',
  'pv',
  'battery',
  'tariff',
  'devices',
  'priorities',
  'mode',
  'summary',
] as const;
export type StepId = (typeof STEP_IDS)[number];

export interface StepDef {
  id: StepId;
  title: MessageKey;
  /** The step can be left without saving ("Skip"). */
  skippable: boolean;
}

export const STEPS: readonly StepDef[] = [
  { id: 'region', title: 'wizard.step.region', skippable: false },
  { id: 'energy', title: 'wizard.step.energy', skippable: true },
  { id: 'grid', title: 'wizard.step.grid', skippable: false },
  { id: 'pv', title: 'wizard.step.pv', skippable: true },
  { id: 'battery', title: 'wizard.step.battery', skippable: false },
  { id: 'tariff', title: 'wizard.step.tariff', skippable: true },
  { id: 'devices', title: 'wizard.step.devices', skippable: true },
  { id: 'priorities', title: 'wizard.step.priorities', skippable: true },
  { id: 'mode', title: 'wizard.step.mode', skippable: false },
  { id: 'summary', title: 'wizard.step.summary', skippable: false },
];

/**
 * The four stages the wizard shows (plan phase-7 WP5): the ten step ids stay as they are, the
 * stages only group them for the progress indicator.
 */
export type StageId = 'basics' | 'system' | 'goals' | 'finish';

export interface StageDef {
  id: StageId;
  title: MessageKey;
  steps: readonly StepId[];
}

export const STAGES: readonly StageDef[] = [
  { id: 'basics', title: 'wizard.stage.basics', steps: ['region', 'energy'] },
  { id: 'system', title: 'wizard.stage.system', steps: ['grid', 'pv', 'battery', 'tariff'] },
  { id: 'goals', title: 'wizard.stage.goals', steps: ['devices', 'priorities'] },
  { id: 'finish', title: 'wizard.stage.finish', steps: ['mode', 'summary'] },
];

/** The stage a step belongs to. */
export function stageOf(id: StepId): StageDef {
  return STAGES.find((s) => s.steps.includes(id))!;
}

export function isStepId(value: string): value is StepId {
  return (STEP_IDS as readonly string[]).includes(value);
}

export function stepIndex(id: StepId): number {
  return STEP_IDS.indexOf(id);
}

export function nextStep(id: StepId): StepId | null {
  return STEP_IDS[stepIndex(id) + 1] ?? null;
}

export function prevStep(id: StepId): StepId | null {
  return STEP_IDS[stepIndex(id) - 1] ?? null;
}

/** Where a resumed wizard continues: the first step not yet completed (the summary at the end). */
export function firstIncompleteStep(completed: readonly string[]): StepId {
  return STEP_IDS.find((id) => !completed.includes(id) && id !== 'summary') ?? 'summary';
}

/** `completed_steps` after finishing `id`: unknown ids are dropped, order follows the flow. */
export function withCompleted(completed: readonly string[], id: StepId): StepId[] {
  const set = new Set<string>([...completed, id]);
  return STEP_IDS.filter((s) => set.has(s));
}
