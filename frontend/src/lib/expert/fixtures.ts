// SPDX-License-Identifier: Apache-2.0
// Test fixtures for the generated expert form.
import type { Param, ParamsApplied } from '../api/types';

export function param(over: Partial<Param> = {}): Param {
  const key = over.key ?? 'controller.tick_s';
  return {
    key,
    type: 'float',
    group: 'controller',
    unit: 's',
    tags: ['expert'],
    default: 5,
    value: 5,
    modified: false,
    minimum: 1,
    maximum: 60,
    choices: [],
    name_key: `param.${key}.name`,
    help_key: `param.${key}.help`,
    see: null,
    restart: false,
    safety: false,
    available: true,
    ...over,
  };
}

export function listing(params: Param[], changed: string[] = []): ParamsApplied {
  return { groups: ['controller', 'planner'], params, changed };
}
