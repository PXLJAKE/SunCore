<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import type { AccuracyRecord, ForecastAccuracyView, PvForecastView } from '../api/types';
  import {
    hasMessage,
    t,
    tDateTime,
    tDynamic,
    tNumber,
    tPercent,
    tPower,
    type MessageKey,
  } from '../i18n/index.svelte';

  interface Props {
    accuracy: ForecastAccuracyView | null;
    pv: PvForecastView | null;
    /** An Open-Meteo based source is configured: its licence asks for an attribution. */
    openMeteo: boolean;
    failed?: boolean;
  }
  let { accuracy, pv, openMeteo, failed = false }: Props = $props();

  const HORIZONS = ['0_6h', '6_24h', '24_48h'] as const;
  const HORIZON_KEY: Record<(typeof HORIZONS)[number], MessageKey> = {
    '0_6h': 'fc.horizon.0_6h',
    '6_24h': 'fc.horizon.6_24h',
    '24_48h': 'fc.horizon.24_48h',
  };
  const CAL_KEY: Record<string, MessageKey> = {
    active: 'fc.cal.active',
    collecting: 'fc.cal.collecting',
    no_history: 'fc.cal.no_history',
    classifier_changed: 'fc.cal.classifier_changed',
  };
  const STATUS_KEY: Record<string, MessageKey> = {
    ok: 'forecast.status.ok',
    partial: 'forecast.status.partial',
    no_data: 'forecast.status.no_data',
    not_configured: 'forecast.status.not_configured',
    not_linked: 'forecast.status.not_linked',
    ambiguous_entry: 'forecast.status.ambiguous_entry',
    entry_not_found: 'forecast.status.entry_not_found',
    error: 'forecast.status.error',
  };

  /** Readable name of a source id: a translated label, the template's name, or the id itself. */
  function sourceName(id: string): string {
    const own = `fc.source.${id}`;
    if (hasMessage(own)) return t(own as MessageKey);
    const tpl = `template.${id}.name`;
    return hasMessage(tpl) ? t(tpl as MessageKey) : id;
  }

  const minSamples = $derived(accuracy?.min_samples ?? 96);
  const rows = $derived(accuracy?.sources ?? []);
  const record = (records: readonly AccuracyRecord[], h: string): AccuracyRecord | undefined =>
    records.find((r) => r.horizon === h);
  const signed = (w: number): string => (w > 0 ? '+' : '') + tPower(w);

  const blended = $derived(pv?.blended ?? null);
  const weightSources = $derived(
    [...new Set((blended?.weights ?? []).flatMap((w) => Object.keys(w.weights)))].sort(),
  );
  const weightFor = (horizon: string, source: string): number | undefined =>
    blended?.weights.find((w) => w.horizon === horizon)?.weights[source];
  const basisKeys = $derived([
    ...new Set((blended?.weights ?? []).map((w) => w.basis).filter((b) => b != null)),
  ]);

  /** "ML vs profile": the optional machine-learning load model next to the profiles. */
  const loadModel = $derived(accuracy?.load_model ?? null);
  const mae = (w: number | null | undefined): string =>
    w == null ? t('fc.ml.not_scored') : tPower(w);
</script>

<section class="card forecast stack" aria-labelledby="fc-title" data-testid="forecast-panel">
  <h3 id="fc-title">{t('fc.title')}</h3>
  <p class="hint">{t('fc.intro')}</p>

  {#if failed}
    <p class="notice error" role="alert">{t('fc.error')}</p>
  {/if}

  {#if pv && pv.sources.length > 0}
    <div class="srcs" data-testid="fc-sources">
      <h4>{t('fc.freshness.title')}</h4>
      <ul>
        {#each pv.sources as s (s.source_id)}
          <li>
            <strong>{sourceName(s.source_id)}</strong>
            <span class="chip-tag" class:warn={s.status !== 'ok'}
              >{t(STATUS_KEY[s.status] ?? 'forecast.status.error')}</span
            >
            {#if s.stale}<span class="chip-tag warn">{t('fc.fresh.stale')}</span>{/if}
            {#if s.fetched_at}
              <span class="hint"
                >{t('fc.fresh.age', { time: tDateTime(new Date(s.fetched_at)) })}</span
              >
            {/if}
          </li>
        {/each}
      </ul>
    </div>
  {/if}

  <div class="scroll">
    <table data-testid="fc-accuracy">
      <caption>{t('fc.accuracy.caption')}</caption>
      <thead>
        <tr>
          <th scope="col">{t('fc.source')}</th>
          {#each HORIZONS as h (h)}<th scope="col">{t(HORIZON_KEY[h])}</th>{/each}
        </tr>
      </thead>
      <tbody>
        {#each rows as r (r.source_id)}
          <tr data-source={r.source_id}>
            <th scope="row">{sourceName(r.source_id)}</th>
            {#each HORIZONS as h (h)}
              {@const rec = record(r.records, h)}
              <td>
                {#if rec}
                  <span class="num"
                    >{t('fc.cell', { mae: tPower(rec.mae_w), bias: signed(rec.bias_w) })}</span
                  >
                  <span class="hint">{t('fc.cell_n', { n: rec.n })}</span>
                  {#if rec.n < minSamples}
                    <span class="chip-tag warn" data-testid="fc-collecting"
                      >{t('fc.collecting', { min: minSamples, n: rec.n })}</span
                    >
                  {/if}
                {:else}
                  <span class="hint">–</span>
                {/if}
              </td>
            {/each}
          </tr>
        {/each}
      </tbody>
    </table>
  </div>
  {#if rows.every((r) => r.records.length === 0)}
    <p class="hint" data-testid="fc-no-records">{t('fc.no_records')}</p>
  {/if}

  {#if blended}
    <div data-testid="fc-weights">
      <h4>{t('fc.weights.title')}</h4>
      {#if weightSources.length <= 1}
        <p class="hint">{t('fc.weights.single')}</p>
      {:else}
        <p class="hint">{t('fc.weights.intro')}</p>
        <div class="scroll">
          <table>
            <caption class="sr-only">{t('fc.weights.title')}</caption>
            <thead>
              <tr>
                <th scope="col">{t('fc.source')}</th>
                {#each blended.weights as w (w.horizon)}
                  <th scope="col"
                    >{t(
                      HORIZON_KEY[w.horizon as (typeof HORIZONS)[number]] ?? 'fc.horizon.48h_plus',
                    )}</th
                  >
                {/each}
              </tr>
            </thead>
            <tbody>
              {#each weightSources as id (id)}
                <tr>
                  <th scope="row">{sourceName(id)}</th>
                  {#each blended.weights as w (w.horizon)}
                    {@const share = weightFor(w.horizon, id)}
                    <td class="num">{share == null ? '–' : tPercent(share * 100)}</td>
                  {/each}
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
        <p class="hint">
          {#each basisKeys as b, i (b)}{i > 0 ? ' · ' : ''}{t(
              b === 'inverse_mae'
                ? 'fc.weights.basis.inverse_mae'
                : 'fc.weights.basis.equal_no_history',
            )}{/each}
          {#if blended.weights.some((w) => w.manual)}
            · {t('fc.weights.manual')}
          {/if}
        </p>
      {/if}
    </div>

    <div data-testid="fc-calibration">
      <h4>{t('fc.calibration.title')}</h4>
      <p class="hint">{t('fc.calibration.intro')}</p>
      <ul>
        {#each blended.calibration as c (c.source_id)}
          <li data-source={c.source_id}>
            <strong>{sourceName(c.source_id)}</strong>
            <span class="chip-tag" class:warn={!c.active}
              >{t(CAL_KEY[c.reason] ?? 'fc.cal.no_history')}</span
            >
            <span class="hint"
              >{t('fc.cal.days', { days: c.days_with_data, min: c.min_history_days })}</span
            >
            {#if c.active && c.factor_min != null && c.factor_max != null}
              <span class="hint"
                >{t('fc.cal.factors', {
                  min: tNumber(c.factor_min, { maximumFractionDigits: 2 }),
                  max: tNumber(c.factor_max, { maximumFractionDigits: 2 }),
                })}</span
              >
            {/if}
          </li>
        {/each}
      </ul>
    </div>
  {/if}

  <div data-testid="fc-ml">
    <h4>{t('fc.ml.title')}</h4>
    <p class="hint">{t('fc.ml.intro')}</p>
    {#if loadModel}
      <p class="tags">
        <span class="chip-tag" data-testid="fc-ml-mode"
          >{t('fc.ml.mode', { mode: loadModel.mode })}</span
        >
        <span class="chip-tag" class:active={loadModel.in_use === 'ml'} data-testid="fc-ml-in-use"
          >{t('fc.ml.in_use', { model: loadModel.in_use })}</span
        >
      </p>
      <p data-testid="fc-ml-reason">{tDynamic(loadModel.reason.key, loadModel.reason.args)}</p>
      {#if loadModel.mode !== 'off'}
        <div class="scroll">
          <table class="compact" data-testid="fc-ml-compare">
            <caption>{t('fc.ml.compare_caption')}</caption>
            <thead>
              <tr>
                <th scope="col">{t('fc.ml.model')}</th>
                <th scope="col">{t('fc.ml.error')}</th>
              </tr>
            </thead>
            <tbody>
              <tr data-model="profile">
                <th scope="row">{t('fc.ml.profile')}</th>
                <td class="num">
                  {mae(loadModel.profile_mae_w)}
                  {#if loadModel.profile_n > 0}<span class="hint"
                      >{t('fc.cell_n', { n: loadModel.profile_n })}</span
                    >{/if}
                </td>
              </tr>
              <tr data-model="ml">
                <th scope="row">{t('fc.ml.ml')}</th>
                <td class="num">
                  {mae(loadModel.ml_mae_w)}
                  {#if loadModel.ml_n > 0}<span class="hint"
                      >{t('fc.cell_n', { n: loadModel.ml_n })}</span
                    >{/if}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <p class="hint" data-testid="fc-ml-days">
          {t('fc.ml.days', { days: loadModel.ml_days, min: loadModel.min_history_days })}
          {#if !loadModel.ml_ready}· {t('fc.ml.collecting_model')}{/if}
        </p>
      {/if}
    {/if}
  </div>

  {#if openMeteo}
    <p class="hint attribution" data-testid="open-meteo-attribution">
      {t('fc.attribution')}
      <a href="https://open-meteo.com/" target="_blank" rel="noopener noreferrer"
        >{t('fc.attribution.link')}</a
      >
      ·
      <a
        href="https://creativecommons.org/licenses/by/4.0/"
        target="_blank"
        rel="noopener noreferrer">{t('fc.attribution.license')}</a
      >
    </p>
  {/if}
</section>

<style>
  h4 {
    font-size: 0.9375rem;
    margin: 0 0 4px;
  }
  ul {
    list-style: none;
    margin: 0;
    padding: 0;
    display: grid;
    gap: 8px;
  }
  li {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 4px 10px;
  }
  .scroll {
    overflow-x: auto;
  }
  table {
    border-collapse: collapse;
    width: 100%;
    min-width: 30rem;
    font-size: 0.9375rem;
  }
  caption {
    text-align: left;
    color: var(--secondary-text-color);
    font-size: 0.875rem;
    padding-bottom: 6px;
  }
  th,
  td {
    text-align: left;
    vertical-align: top;
    padding: 6px 10px 6px 0;
    border-top: 1px solid var(--divider-color);
  }
  thead th {
    border-top: 0;
    font-weight: 600;
  }
  td .hint,
  td .chip-tag {
    display: block;
  }
  td .chip-tag {
    margin-top: 2px;
    width: fit-content;
  }
  .num {
    font-variant-numeric: tabular-nums;
  }
  table.compact {
    min-width: 0;
  }
  .tags {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    margin: 0 0 6px;
  }
  .chip-tag.active {
    font-weight: 600;
  }
  .attribution a {
    overflow-wrap: anywhere;
  }
</style>
