// SPDX-License-Identifier: Apache-2.0
// Forecast section <-> form: the PV forecast sources (templates plus the entities of their roles).
// Independent of the tariff, so the choice can be stored without prices.
import type { ForecastConfig } from '../api/types';
import { emptyRef, fromRef, toRef, type RefField } from './forms';

export interface ForecastForm {
  templateIds: string[];
  /** Forecast role -> entity; rows without an entity are not sent. */
  entities: Record<string, RefField>;
}

export function emptyForecastForm(): ForecastForm {
  return { templateIds: [], entities: {} };
}

export function forecastFormFromConfig(cfg: ForecastConfig | null): ForecastForm {
  if (!cfg) return emptyForecastForm();
  return {
    templateIds: [...(cfg.forecast_template_ids ?? [])],
    entities: Object.fromEntries(
      Object.entries(cfg.forecast_entities ?? {}).map(([role, ref]) => [role, fromRef(ref)]),
    ),
  };
}

export function forecastFromForm(form: ForecastForm): ForecastConfig {
  const entities: ForecastConfig['forecast_entities'] = {};
  for (const [role, field] of Object.entries(form.entities)) {
    const ref = toRef(field);
    if (ref) entities[role] = ref;
  }
  return {
    schema_version: 1,
    forecast_template_ids: [...form.templateIds],
    forecast_entities: entities,
  };
}

/** Toggle a forecast template; entities of its roles are dropped when it is switched off. */
export function setForecastTemplate(
  form: ForecastForm,
  id: string,
  on: boolean,
  roles: readonly string[],
): void {
  const has = form.templateIds.includes(id);
  if (on && !has) form.templateIds.push(id);
  if (!on && has) {
    form.templateIds = form.templateIds.filter((x) => x !== id);
    for (const role of roles) delete form.entities[role];
  }
}

/** Give every role of a chosen template a (possibly empty) field to render. */
export function ensureRoleFields(form: ForecastForm, roles: readonly string[]): void {
  for (const role of roles) form.entities[role] ??= emptyRef();
}
