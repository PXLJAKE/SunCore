// SPDX-License-Identifier: Apache-2.0
// Forecast section (v2) <-> form: the PV forecast sources (templates plus the entities of their
// roles). Independent of the tariff, so the choice can be stored without prices. Weights and
// learning settings have no form fields yet (Phase 4 WP10); they are carried through unchanged.
import type { ForecastConfig } from '../api/types';
import { emptyRef, fromRef, toRef, type RefField } from './forms';

type Source = NonNullable<ForecastConfig['sources']>[number];

export interface ForecastForm {
  templateIds: string[];
  /** Forecast role -> entity; rows without an entity are not sent. */
  entities: Record<string, RefField>;
  /** Roles per template id, when known; a template without an entry gets every entity. */
  roles: Record<string, string[]>;
  /** Stored weighting per template id (carried through). */
  weights: Record<string, Pick<Source, 'weight_mode' | 'weight'>>;
  learning: ForecastConfig['learning'] | null;
}

export function emptyForecastForm(): ForecastForm {
  return { templateIds: [], entities: {}, roles: {}, weights: {}, learning: null };
}

export function forecastFormFromConfig(cfg: ForecastConfig | null): ForecastForm {
  if (!cfg) return emptyForecastForm();
  const form = emptyForecastForm();
  form.learning = cfg.learning;
  for (const source of cfg.sources ?? []) {
    form.templateIds.push(source.template_id);
    form.weights[source.template_id] = { weight_mode: source.weight_mode, weight: source.weight };
    for (const [role, ref] of Object.entries(source.entities ?? {})) {
      form.entities[role] ??= fromRef(ref);
    }
  }
  return form;
}

export function forecastFromForm(form: ForecastForm): ForecastConfig {
  const entities: Record<string, NonNullable<ReturnType<typeof toRef>>> = {};
  for (const [role, field] of Object.entries(form.entities)) {
    const ref = toRef(field);
    if (ref) entities[role] = ref;
  }
  const sources: Source[] = form.templateIds.map((id) => {
    const roles = form.roles[id];
    const own = roles
      ? Object.fromEntries(Object.entries(entities).filter(([role]) => roles.includes(role)))
      : { ...entities };
    const weight = form.weights[id];
    return {
      template_id: id,
      entities: own,
      weight_mode: weight?.weight_mode ?? 'auto',
      weight: weight?.weight ?? null,
    };
  });
  return {
    schema_version: 2,
    sources,
    learning: form.learning ?? { min_history_days: 30, load_ml_mode: 'shadow' },
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
  if (on) form.roles[id] = [...roles];
  if (on && !has) form.templateIds.push(id);
  if (!on && has) {
    form.templateIds = form.templateIds.filter((x) => x !== id);
    for (const role of roles) delete form.entities[role];
    delete form.roles[id];
    delete form.weights[id];
  }
}

/** Give every role of a chosen template a (possibly empty) field to render. */
export function ensureRoleFields(form: ForecastForm, roles: readonly string[]): void {
  for (const role of roles) form.entities[role] ??= emptyRef();
}
