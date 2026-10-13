// SPDX-License-Identifier: Apache-2.0
import { describe, expect, it } from 'vitest';
import { issuesAt, locPath, scopeIssues, unclaimed } from './issues';
import { firstIncompleteStep, nextStep, prevStep, STEPS, STEP_IDS, withCompleted } from './steps';

describe('steps', () => {
  it('lists every step once and in order', () => {
    expect(STEPS.map((s) => s.id)).toEqual([...STEP_IDS]);
    expect(nextStep('region')).toBe('energy');
    expect(prevStep('region')).toBeNull();
    expect(nextStep('summary')).toBeNull();
  });
  it('resumes at the first step that is not done', () => {
    expect(firstIncompleteStep([])).toBe('region');
    expect(firstIncompleteStep(['region', 'energy', 'grid'])).toBe('pv');
    expect(firstIncompleteStep(['region', 'grid'])).toBe('energy');
    expect(firstIncompleteStep([...STEP_IDS])).toBe('summary');
  });
  it('keeps completed steps ordered and drops unknown ones', () => {
    expect(withCompleted(['grid', 'bogus'], 'region')).toEqual(['region', 'grid']);
    expect(withCompleted(['region'], 'region')).toEqual(['region']);
  });
});

describe('issues', () => {
  const issue = (loc: (string | number)[], key = 'api.validation.missing') => ({
    loc,
    key,
    args: {},
  });
  const list = [
    issue(['body', 'grid', 'power', 'entity_id']),
    issue(['body', 'grid']),
    issue(['body', 'devices', 1, 'entities', 'power']),
  ];
  it('drops the body prefix', () => {
    expect(locPath(list[0]!)).toEqual(['grid', 'power', 'entity_id']);
  });
  it('matches issues at or below a prefix', () => {
    expect(issuesAt(list, 'grid')).toHaveLength(2);
    expect(issuesAt(list, 'grid', 'power')).toHaveLength(1);
    expect(issuesAt(list, 'devices', 1)).toHaveLength(1);
    expect(issuesAt(list, 'devices', 0)).toHaveLength(0);
  });
  it('re-roots the issues of one device', () => {
    const scoped = scopeIssues(list, 'devices', 1);
    expect(scoped[0]!.loc).toEqual(['body', 'entities', 'power']);
    expect(issuesAt(scoped, 'entities', 'power')).toHaveLength(1);
  });
  it('finds issues no field claims', () => {
    expect(unclaimed(list, [['grid', 'power']])).toHaveLength(2);
  });
});
