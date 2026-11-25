// SPDX-License-Identifier: Apache-2.0
// Read-only mirror of tokens.css for code that cannot use CSS (the ECharts canvas). The source of
// truth is tokens.css; the values here are the light defaults, used only when a token cannot be
// read (no stylesheet, as in unit tests). tokens.test.ts keeps them equal to tokens.css.

/** Tokens the charts read, with the light default of each. */
export const CHART_TOKEN_DEFAULTS = {
  '--sc-text': '#1f2328',
  '--sc-text-muted': '#57606a',
  '--sc-outline': '#dde1e6',
  '--sc-surface': '#ffffff',
  '--sc-surface-raised': '#ffffff',
  '--sc-accent': '#0369a1',
  '--sc-warn': '#8a5100',
  '--sc-energy-pv': '#b45f06',
  '--sc-energy-grid-in': '#2f6fa8',
  '--sc-energy-grid-out': '#7040b8',
  '--sc-energy-bat-in': '#00796b',
  '--sc-energy-bat-out': '#c2185b',
  '--sc-energy-house': '#4b5563',
  '--sc-energy-device': '#4d7c0f',
} as const;

export type ChartToken = keyof typeof CHART_TOKEN_DEFAULTS;

/** Reads the chart tokens from the computed style of `node` (custom properties inherit). */
export function readTokens(node: Element): Record<ChartToken, string> {
  const css = getComputedStyle(node);
  const out = {} as Record<ChartToken, string>;
  for (const [name, fallback] of Object.entries(CHART_TOKEN_DEFAULTS) as [ChartToken, string][]) {
    out[name] = css.getPropertyValue(name).trim() || fallback;
  }
  return out;
}
