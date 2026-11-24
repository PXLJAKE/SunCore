// SPDX-License-Identifier: Apache-2.0
// Zoom and pan of the chart in percent of the window, for the keyboard (arrow keys, `+`, `-`,
// `0`) and the buttons. Pure so the rules are testable without a canvas.

export interface ZoomRange {
  /** Start and end in percent of the whole window, `0 <= start < end <= 100`. */
  start: number;
  end: number;
}

export const FULL: ZoomRange = { start: 0, end: 100 };
/** The visible part never gets smaller than this share of the window. */
export const MIN_SPAN_PCT = 4;

const clamp = (r: ZoomRange): ZoomRange => {
  const span = Math.min(100, Math.max(MIN_SPAN_PCT, r.end - r.start));
  const start = Math.min(100 - span, Math.max(0, r.start));
  return { start, end: start + span };
};

export type ZoomAction = 'in' | 'out' | 'left' | 'right' | 'reset';

/** The range after an action: zoom around the centre, pan by a quarter of the visible span. */
export function applyZoom(range: ZoomRange, action: ZoomAction): ZoomRange {
  const span = range.end - range.start;
  const centre = (range.start + range.end) / 2;
  switch (action) {
    case 'in':
      return clamp({ start: centre - span / 3, end: centre + span / 3 });
    case 'out':
      return clamp({ start: centre - span * 0.75, end: centre + span * 0.75 });
    case 'left':
      return clamp({ start: range.start - span / 4, end: range.end - span / 4 });
    case 'right':
      return clamp({ start: range.start + span / 4, end: range.end + span / 4 });
    case 'reset':
      return FULL;
  }
}

/** The action a key stands for (`null` = not ours). */
export function zoomKey(key: string): ZoomAction | null {
  switch (key) {
    case '+':
    case '=':
      return 'in';
    case '-':
    case '_':
      return 'out';
    case 'ArrowLeft':
      return 'left';
    case 'ArrowRight':
      return 'right';
    case '0':
    case 'Home':
      return 'reset';
    default:
      return null;
  }
}

export const isZoomed = (r: ZoomRange): boolean => r.start > 0.05 || r.end < 99.95;

/** The visible time span of a window for a zoom range (ms). */
export function visibleSpan(fromMs: number, toMs: number, r: ZoomRange): [number, number] {
  const span = toMs - fromMs;
  return [fromMs + (span * r.start) / 100, fromMs + (span * r.end) / 100];
}
