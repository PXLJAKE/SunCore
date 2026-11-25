// SPDX-License-Identifier: Apache-2.0
// Pure helpers for the EV, heat pump and program parts of a device card (PR 6-M).
import type {
  DeviceConfig,
  DeviceControlState,
  EvMode,
  EvTarget,
  JobState,
  ProgramJob,
  ThermalSpec,
} from '../api/types';

/** Order of the EV mode buttons (core/flex.py `EvMode`). */
export const EV_MODES: readonly EvMode[] = ['off', 'now', 'min_pv', 'pv', 'target'];

/** What a device card shows beyond the common part. */
export type FlexKind = 'ev' | 'program' | 'heat_pump' | null;

/** Which flex section a device gets, judged by its configured control and specs. */
export function flexKind(config: DeviceConfig | undefined): FlexKind {
  if (!config) return null;
  if (config.program) return 'program';
  const k = config.control.kind;
  if (k === 'charger' || k === 'loadpoint') return 'ev';
  if (k === 'sg_ready' || (config.kind === 'heat_pump' && k === 'setpoint')) return 'heat_pump';
  return null;
}

/** A program that a smart plug starts: cancelling or releasing it switches the plug ON. */
export function startsOnRelease(config: DeviceConfig | undefined): boolean {
  return config?.program?.start_method === 'plug';
}

/** Value of a `<datetime-local>` input for a date (local time, minute precision). */
export function toLocalInput(date: Date): string {
  const pad = (n: number): string => String(n).padStart(2, '0');
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}` +
    `T${pad(date.getHours())}:${pad(date.getMinutes())}`
  );
}

/** Default "finished by" suggestion: the next 07:00 that is at least `minHours` away. */
export function defaultDeadline(now: Date, minHours = 2): Date {
  const at = new Date(now);
  at.setHours(7, 0, 0, 0);
  while (at.getTime() < now.getTime() + minHours * 3_600_000) at.setDate(at.getDate() + 1);
  return at;
}

export type DeadlineError = 'invalid' | 'past';

export type DeadlineResult = { ok: true; iso: string } | { ok: false; error: DeadlineError };

/** `<datetime-local>` text (the user's local time) to an absolute instant in the future. */
export function parseDeadline(text: string, now: Date): DeadlineResult {
  const value = text.trim();
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?$/.test(value))
    return { ok: false, error: 'invalid' };
  const at = new Date(value);
  if (Number.isNaN(at.getTime())) return { ok: false, error: 'invalid' };
  if (at.getTime() <= now.getTime()) return { ok: false, error: 'past' };
  return { ok: true, iso: at.toISOString() };
}

export type TargetKind = 'soc' | 'energy';
export type TargetError = 'invalid' | 'past' | 'value';

export type TargetResult = { ok: true; target: EvTarget } | { ok: false; error: TargetError };

/**
 * The EV target of the form: a state of charge (1..100 %) or an energy in kWh, by a time.
 * The energy is sent in Wh.
 */
export function evTarget(
  kind: TargetKind,
  value: number | null,
  by: string,
  now: Date,
): TargetResult {
  const when = parseDeadline(by, now);
  if (!when.ok) return { ok: false, error: when.error };
  if (value === null || !Number.isFinite(value) || value <= 0) return { ok: false, error: 'value' };
  if (kind === 'soc') {
    if (value > 100) return { ok: false, error: 'value' };
    return { ok: true, target: { soc_pct: value, energy_wh: null, by: when.iso } };
  }
  if (value > 500) return { ok: false, error: 'value' };
  return { ok: true, target: { soc_pct: null, energy_wh: Math.round(value * 1000), by: when.iso } };
}

/** The fields of an existing target back into the form. */
export function targetToForm(
  target: EvTarget | null | undefined,
): { kind: TargetKind; value: number | null; by: string } | null {
  if (!target) return null;
  const by = toLocalInput(new Date(target.by));
  if (target.soc_pct != null) return { kind: 'soc', value: target.soc_pct, by };
  return { kind: 'energy', value: target.energy_wh != null ? target.energy_wh / 1000 : null, by };
}

/** Phase state text key part: `charging`, `unplugged` ... from what the charger reports. */
export type EvPlug = 'unplugged' | 'plugged' | 'unknown';

export function evPlug(state: DeviceControlState['state']): EvPlug {
  if (!state || state.connected == null) return 'unknown';
  return state.connected ? 'plugged' : 'unplugged';
}

/** SG-Ready states 1..4 (1 = utility block, never written by SunCore). */
export function sgState(step: number | null | undefined): 1 | 2 | 3 | 4 | null {
  return step === 1 || step === 2 || step === 3 || step === 4 ? step : null;
}

/** Lift of the set-point over the base set-point, in K; `null` when either is unknown. */
export function liftK(
  device: Pick<DeviceControlState, 'state' | 'base_setpoint_c'>,
): number | null {
  const sp = device.state?.setpoint_c;
  const base = device.base_setpoint_c;
  if (sp == null || base == null) return null;
  return Math.round((sp - base) * 10) / 10;
}

export interface ThermalView {
  /** Lowest and highest COP of the curve, `null` without a curve. */
  cop: { min: number; max: number; assumed: boolean } | null;
  /** Fit error of the learned model as a fraction, `null` while none is learned. */
  nrmse: number | null;
}

export function thermalView(spec: ThermalSpec | null | undefined): ThermalView {
  const points = spec?.cop?.points ?? [];
  const cops = points.map((p) => p.cop);
  return {
    cop:
      spec?.cop && cops.length > 0
        ? { min: Math.min(...cops), max: Math.max(...cops), assumed: spec.cop.assumed }
        : null,
    nrmse: spec?.learned?.nrmse ?? null,
  };
}

/** Whether a job still can be cancelled by the user (not final). */
export function jobOpen(job: ProgramJob | null | undefined): job is ProgramJob {
  return job != null && job.state !== 'done' && job.state !== 'cancelled';
}

export const JOB_STATES: readonly JobState[] = [
  'armed',
  'scheduled',
  'running',
  'done',
  'cancelled',
];
