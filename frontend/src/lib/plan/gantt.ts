// SPDX-License-Identifier: Apache-2.0
// The Gantt rows under the plan chart: per controlled device when the plan lets it run (band
// preference) and when it ran (`device.<id>.on` of the history), and for the battery the planned
// and the measured charge / discharge / lock periods. Pure, no ECharts.
import type { PlanInterval } from '../api/types';
import { actualIndex, type ActualData, type Values } from './actual';

/** Below this the battery counts as idle (W). */
export const BATTERY_IDLE_W = 50;

export type SegKind =
  | 'prefer_on'
  | 'allowed'
  | 'prefer_off'
  | 'charge'
  | 'discharge'
  | 'lock'
  | 'run'
  | 'partial'
  | 'start';

/** Fixed order: the index is what the chart's colour tables use. */
export const SEG_KINDS: readonly SegKind[] = [
  'prefer_on',
  'allowed',
  'prefer_off',
  'charge',
  'discharge',
  'lock',
  'run',
  'partial',
  'start',
];

export interface GanttSeg {
  fromMs: number;
  toMs: number;
  kind: SegKind;
}

export interface GanttLane {
  /** Device id, or `battery`. */
  id: string;
  battery: boolean;
  planned: GanttSeg[];
  actual: GanttSeg[];
}

export const BATTERY_LANE = 'battery';

/** Merge neighbours of the same kind that touch. */
function merge(segs: GanttSeg[]): GanttSeg[] {
  const out: GanttSeg[] = [];
  for (const s of segs) {
    const last = out[out.length - 1];
    if (last && last.kind === s.kind && last.toMs >= s.fromMs) last.toMs = s.toMs;
    else out.push({ ...s });
  }
  return out;
}

/** Ids of the devices the plan has a band for, in the order they first appear. */
export function planDeviceIds(intervals: readonly PlanInterval[]): string[] {
  const seen = new Set<string>();
  for (const i of intervals) for (const id of Object.keys(i.devices ?? {})) seen.add(id);
  return [...seen];
}

function plannedDevice(intervals: readonly PlanInterval[], id: string): GanttSeg[] {
  const segs: GanttSeg[] = [];
  for (const i of intervals) {
    const band = i.devices?.[id];
    if (!band?.allowed) continue;
    // A program's start slot (the planner picks it) beats the band preference.
    const kind: SegKind = band.start
      ? 'start'
      : band.preference === 'prefer_on'
        ? 'prefer_on'
        : band.preference === 'prefer_off'
          ? 'prefer_off'
          : 'allowed';
    segs.push({ fromMs: Date.parse(i.start), toMs: Date.parse(i.end), kind });
  }
  return merge(segs);
}

function plannedBattery(intervals: readonly PlanInterval[]): GanttSeg[] {
  const segs: GanttSeg[] = [];
  for (const i of intervals) {
    const w = i.expected?.battery_w ?? 0;
    const kind: SegKind | null =
      w > BATTERY_IDLE_W
        ? 'charge'
        : i.battery?.discharge_lock
          ? 'lock'
          : w < -BATTERY_IDLE_W
            ? 'discharge'
            : null;
    if (kind) segs.push({ fromMs: Date.parse(i.start), toMs: Date.parse(i.end), kind });
  }
  return merge(segs);
}

function actualSegs(a: ActualData, values: Values, kindOf: (v: number) => SegKind | null) {
  const segs: GanttSeg[] = [];
  a.times.forEach((from, i) => {
    const v = values[i];
    const kind = v == null ? null : kindOf(v);
    if (kind) segs.push({ fromMs: from, toMs: from + a.stepMs, kind });
  });
  return merge(segs);
}

const runKind = (share: number): SegKind | null =>
  share >= 0.5 ? 'run' : share > 0 ? 'partial' : null;
const batteryKind = (w: number): SegKind | null =>
  w > BATTERY_IDLE_W ? 'charge' : w < -BATTERY_IDLE_W ? 'discharge' : null;

/**
 * The lanes: the battery first (when the plan has battery bands), then every device the plan or
 * the measurements know. A lane without anything to show is left out.
 */
export function buildLanes(
  intervals: readonly PlanInterval[],
  actual: ActualData | null,
  extraDeviceIds: readonly string[] = [],
): GanttLane[] {
  const lanes: GanttLane[] = [];
  const battery: GanttLane = {
    id: BATTERY_LANE,
    battery: true,
    planned: plannedBattery(intervals),
    actual: actual ? actualSegs(actual, actual.batteryW, batteryKind) : [],
  };
  if (battery.planned.length > 0 || battery.actual.length > 0) lanes.push(battery);
  const ids = new Set([...planDeviceIds(intervals), ...extraDeviceIds]);
  for (const id of ids) {
    const lane: GanttLane = {
      id,
      battery: false,
      planned: plannedDevice(intervals, id),
      actual: actual?.devices[id] ? actualSegs(actual, actual.devices[id], runKind) : [],
    };
    if (lane.planned.length > 0 || lane.actual.length > 0) lanes.push(lane);
  }
  return lanes;
}

/** The segment of a lane that covers `ms` (planned or actual side), if any. */
export function segAt(segs: readonly GanttSeg[], ms: number): GanttSeg | null {
  return segs.find((s) => s.fromMs <= ms && ms < s.toMs) ?? null;
}

/** On/off share of a device at `ms` from the measurements; `null` without data. */
export function deviceShareAt(a: ActualData | null, id: string, ms: number): number | null {
  if (!a) return null;
  const i = actualIndex(a, ms);
  return i < 0 ? null : (a.devices[id]?.[i] ?? null);
}
