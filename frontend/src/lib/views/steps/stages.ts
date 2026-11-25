// SPDX-License-Identifier: Apache-2.0
// The wizard steps, one lazy chunk per stage (plan phase-7 §8, 7-G): a chunk per step made ten
// chunks plus a dozen tiny shared ones, each with its own import list and gzip overhead.
import type { Component } from 'svelte';
import type { StageId, StepId } from '../../wizard/steps';

type StageModule = Partial<Record<StepId, Component>>;

export const STAGE_LOADERS: Record<StageId, () => Promise<StageModule>> = {
  basics: () => import('./stageBasics'),
  system: () => import('./stageSystem'),
  goals: () => import('./stageGoals'),
  finish: () => import('./stageFinish'),
};
