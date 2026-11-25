// SPDX-License-Identifier: Apache-2.0
// Rendering of backend reasons (`Reason(key, args)`): the planner and controller put the device
// *id* into `args.device` (they do not know display names); the panel shows the configured name.
import type { Reason } from '../api/types';
import { i18n, tDynamic } from '../i18n/index.svelte';
import { app } from '../stores/app.svelte';

/** The display name of a configured device, the id itself when it is not configured. */
export function deviceName(id: string): string {
  return app.devices?.devices?.find((d) => d.id === id)?.name ?? id;
}

/** `args` with a `device` id replaced by its display name (other arguments unchanged). */
export function withDeviceName(
  args: Reason['args'] | undefined,
  name: (id: string) => string = deviceName,
): Reason['args'] | undefined {
  const id = args?.['device'];
  return typeof id === 'string' ? { ...args, device: name(id) } : args;
}

/** First letter upper case in the given locale (a sentence that starts with a name or id). */
export function capitalise(text: string, locale: string): string {
  const first = text.charAt(0);
  return first ? first.toLocaleUpperCase(locale) + text.slice(1) : text;
}

/** One reason as a sentence: device names instead of ids, starting with a capital. */
export function tReason(r: Pick<Reason, 'key' | 'args'>): string {
  return capitalise(tDynamic(r.key, withDeviceName(r.args)), i18n.locale);
}
