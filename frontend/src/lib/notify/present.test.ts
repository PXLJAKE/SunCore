// SPDX-License-Identifier: Apache-2.0
import { describe, expect, it } from 'vitest';
import { addTarget, MAX_TARGETS, removeTarget } from './present';

describe('notification targets', () => {
  it('adds a target once and trims it', () => {
    const one = addTarget([], 'service', ' mobile_app_phone ');
    expect(one).toEqual([{ kind: 'service', id: 'mobile_app_phone' }]);
    expect(addTarget(one, 'service', 'mobile_app_phone')).toEqual(one);
    expect(addTarget(one, 'entity', 'mobile_app_phone')).toHaveLength(2);
    expect(addTarget(one, 'service', '  ')).toEqual(one);
  });

  it('stops at the maximum', () => {
    let list = addTarget([], 'service', 's0');
    for (let i = 1; i < MAX_TARGETS + 2; i += 1) list = addTarget(list, 'service', `s${i}`);
    expect(list).toHaveLength(MAX_TARGETS);
  });

  it('removes by kind and id', () => {
    const list = addTarget(addTarget([], 'service', 'a'), 'entity', 'notify.b');
    expect(removeTarget(list, { kind: 'service', id: 'a' })).toEqual([
      { kind: 'entity', id: 'notify.b' },
    ]);
  });
});
