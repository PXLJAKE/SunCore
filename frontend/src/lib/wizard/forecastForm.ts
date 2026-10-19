// SPDX-License-Identifier: Apache-2.0
// Forecast section (v2) <-> form: the PV forecast sources (templates plus the entities of their
// roles). Independent of the tariff, so the choice can be stored without prices. Weights and
// learning settings have no form fields yet (Phase 4 WP10); they are carried through unchanged.
import type { ForecastConfig } from '../api/types';
import { emptyRef, fromRef, toRef, type RefField } from './forms';

type Source = NonNullable<ForecastConfig['sources']>[number];

export interface ForecastForm {
  templateIds: string[];
  /** Per source (template id): role -> entity. Keyed per source, so two templates that use the
   * same role name (e.g. `today`) keep their own entities. Rows without an entity are not sent. */
  entities: Record<string, Record<string, RefField>>;
  /** Stored weighting per template id (carried through). */
  weights: Record<string, Pick<Source, 'weight_mode' | 'weight'>>;
  learning: ForecastConfig['learning'] | null;
}

export function emptyForecastForm(): ForecastForm {
  return { templateIds: [], entities: {}, weights: {}, learning: null };
}

export function forecastFormFromConfig(cfg: ForecastConfig | null): ForecastForm {
  if (!cfg) return emptyForecastForm();
  const form = emptyForecastForm();
  form.learning = cfg.learning;
  for (const source of cfg.sources ?? []) {
    const id = source.template_id;
    form.templateIds.push(id);
    form.weights[id] = { weight_mode: source.weight_mode, weight: source.weight };
    form.entities[id] = Object.fromEntries(
      Object.entries(source.entities ?? {}).map(([role, ref]) => [role, fromRef(ref)]),
    );
  }
  return form;
}

export function forecastFromForm(form: ForecastForm): ForecastConfig {
  const sources: Source[] = form.templateIds.map((id) => {
    const own: Record<string, NonNullable<ReturnType<typeof toRef>>> = {};
    for (const [role, field] of Object.entries(form.entities[id] ?? {})) {
      const ref = toRef(field);
      if (ref) own[role] = ref;
    }
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

/** Toggle a forecast template; its entities are dropped when it is switched off. */
export function setForecastTemplate(
  form: ForecastForm,
  id: string,
  on: boolean,
  roles: readonly string[],
): void {
  const has = form.templateIds.includes(id);
  if (on) {
    if (!has) form.templateIds.push(id);
    ensureRoleFields(form, id, roles);
  } else if (has) {
    form.templateIds = form.templateIds.filter((x) => x !== id);
    delete form.entities[id];
    delete form.weights[id];
  }
}

/** Give every role of a chosen template a (possibly empty) field to render. */
export function ensureRoleFields(form: ForecastForm, id: string, roles: readonly string[]): void {
  const own = (form.entities[id] ??= {});
  for (const role of roles) own[role] ??= emptyRef();
}
