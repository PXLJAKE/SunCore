// SPDX-License-Identifier: Apache-2.0
import { describe, expect, it } from 'vitest';
import { CHART_TOKEN_DEFAULTS } from '../theme/tokens';
import {
  chartDefaults,
  chartTheme,
  historyColors,
  planColors,
  readChartTheme,
  withChartTheme,
} from './theme';

const th = chartTheme({ ...CHART_TOKEN_DEFAULTS });

describe('chart theme', () => {
  it('builds the option defaults from the tokens', () => {
    expect(chartDefaults(th)).toMatchSnapshot();
  });

  it('gives plan and history charts one energy palette', () => {
    const plan = planColors(th);
    const history = historyColors(th);
    expect(plan.pv).toBe(CHART_TOKEN_DEFAULTS['--sc-energy-pv']);
    expect(history.pv).toBe(plan.pv);
    expect(history.gridImport).toBe(plan.gridImport);
    expect(history.gridExport).toBe(plan.gridExport);
    expect(history.battery).toBe(plan.battery);
    expect(history.house).toBe(plan.load);
    expect(plan.devices).toBe(CHART_TOKEN_DEFAULTS['--sc-energy-device']);
  });

  it('reads the tokens of a node and falls back to the light defaults', () => {
    const el = document.createElement('div');
    el.style.setProperty('--sc-energy-pv', '#ffb74d');
    document.body.append(el);
    const read = readChartTheme(el);
    expect(read.energy.pv).toBe('#ffb74d');
    expect(read.text).toBe(CHART_TOKEN_DEFAULTS['--sc-text']);
    el.remove();
  });

  it('merges under the builder: the builder wins, the theme fills the gaps', () => {
    const option = withChartTheme(
      {
        textStyle: { color: 'builder' },
        tooltip: { trigger: 'axis', extraCssText: 'max-width: 10rem;' },
        legend: [{ top: 0 }],
        xAxis: { type: 'time', axisLabel: { formatter: 'f' } },
        yAxis: [{ type: 'value', axisLabel: { show: false } }],
        series: [],
      },
      th,
    ) as Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any
    expect(option.textStyle).toEqual({ color: 'builder', fontSize: 12 });
    expect(option.tooltip.trigger).toBe('axis');
    expect(option.tooltip.backgroundColor).toBe(th.raised);
    expect(option.tooltip.extraCssText).toBe(
      'max-width: 10rem; border-radius: var(--sc-radius-md); box-shadow: var(--sc-elev-2);',
    );
    expect(option.legend[0]).toMatchObject({ top: 0, icon: 'roundRect', inactiveColor: th.muted });
    expect(option.xAxis.axisLabel).toEqual({
      color: th.muted,
      fontSize: 12,
      hideOverlap: true,
      formatter: 'f',
    });
    expect(option.yAxis[0].axisLabel.show).toBe(false);
    expect(option.series).toEqual([]);
  });

  it('adds no tooltip or legend the builder did not ask for', () => {
    const option = withChartTheme({ series: [] }, th) as Record<string, unknown>;
    expect(option['tooltip']).toBeUndefined();
    expect(option['legend']).toBeUndefined();
  });
});
