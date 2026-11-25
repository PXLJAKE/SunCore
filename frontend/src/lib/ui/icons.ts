// SPDX-License-Identifier: Apache-2.0
// Inline SVG icons (ADR-0019: no icon font, no icon package). Original line drawings made for
// SunCore on a 24 px grid, stroke 2, round caps; licensed like the project (Apache-2.0, see
// NOTICE). Each value is the `d` of one stroked path.

const CIRCLE = 'M21 12a9 9 0 1 1-18 0a9 9 0 1 1 18 0Z';

export const ICONS = {
  /** The product mark: a sun. */
  sun: 'M16 12a4 4 0 1 1-8 0a4 4 0 1 1 8 0Z M12 2.5v2 M12 19.5v2 M2.5 12h2 M19.5 12h2 M5.3 5.3l1.4 1.4 M17.3 17.3l1.4 1.4 M5.3 18.7l1.4-1.4 M17.3 6.7l1.4-1.4',
  /** Now: live power. */
  now: 'M13 2.5 4.5 13.5h6.5l-1 8 8.5-11h-6.5l1-8Z',
  /** Plan: a calendar with a timeline. */
  plan: 'M4 5.5h16v15H4Z M4 10h16 M8.5 3v4 M15.5 3v4 M7.5 14h4 M7.5 17.5h8',
  /** Analysis: a bar chart. */
  analysis: 'M3.5 20.5h17 M7 17v-5 M12 17V7 M17 17v-7.5',
  /** Settings: sliders. */
  settings: 'M4 7h9 M18 7h2 M4 17h2 M11 17h9 M15.5 4.5v5 M8.5 14.5v5',
  /** Emergency stop: an octagon with a bar. */
  stop: 'M8.3 2.8h7.4l5.5 5.5v7.4l-5.5 5.5H8.3l-5.5-5.5V8.3Z M8 12h8',
  /** Expert mode: a flask. */
  expert:
    'M9 3h6 M10 3v6.5L4.8 18.2A1.9 1.9 0 0 0 6.4 21h11.2a1.9 1.9 0 0 0 1.6-2.8L14 9.5V3 M7.3 15h9.4',
  info: `${CIRCLE} M12 11v5.5 M12 7.5h.01`,
  ok: `${CIRCLE} M8 12.5l2.7 2.7L16.2 9.6`,
  warn: 'M12 3.5 2.5 20h19Z M12 10v4.5 M12 17.3h.01',
  error: `${CIRCLE} M12 7v6.5 M12 16.5h.01`,
  chevron: 'M6 9l6 6 6-6',
  empty: 'M3 13.5h5l1.5 3h5l1.5-3h5 M5.5 5h13l2.5 8.5V19H3v-5.5Z',
} as const;

export type IconName = keyof typeof ICONS;
