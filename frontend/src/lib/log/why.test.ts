// SPDX-License-Identifier: Apache-2.0
import { describe, expect, it } from 'vitest';
import { inputLabel, inputValue } from './why';

const f = {
  power: (w: number) => `${w} W`,
  percent: (p: number) => `${p} %`,
  number: (v: number) => String(v),
  none: '-',
};

describe('why panel readings', () => {
  it('labels site and device readings', () => {
    expect(inputLabel('site.grid_w')).toEqual({ key: 'why.input.grid', device: null });
    expect(inputLabel('device.pool_pump.power_w')).toEqual({
      key: 'why.input.device_power',
      device: 'pool_pump',
    });
    expect(inputLabel('device.a.b.on')).toEqual({ key: 'why.input.device_on', device: 'a.b' });
    expect(inputLabel('weather.x').key).toBe('why.input.other');
  });

  it('formats by unit and shows missing data', () => {
    expect(inputValue({ value: 1200, unit: 'W' }, f)).toBe('1200 W');
    expect(inputValue({ value: 55, unit: '%' }, f)).toBe('55 %');
    expect(inputValue({ value: 0.5, unit: 'share' }, f)).toBe('50 %');
    expect(inputValue({ value: null, unit: 'W' }, f)).toBe('-');
    expect(inputValue({ value: 3, unit: 'x' }, f)).toBe('3');
  });
});
