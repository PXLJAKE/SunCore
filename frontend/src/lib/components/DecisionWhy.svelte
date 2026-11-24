<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import type { Decision, DecisionExplain } from '../api/types';
  import { batteryParts, describeAction } from '../control/actions';
  import {
    t,
    tDateTime,
    tDynamic,
    tNumber,
    tPercent,
    tPower,
    tTime,
    type MessageKey,
  } from '../i18n/index.svelte';
  import { logHash } from '../log/filters';
  import { inputLabel, inputValue } from '../log/why';
  import type { ExplainState } from '../stores/decisionLog.svelte';

  interface Props {
    state: ExplainState;
    /** Device id -> name. */
    names: ReadonlyMap<string, string>;
    /** The decision's device is a card on the Now view. */
    hasCard: boolean;
    onretry: () => void;
    onclose: () => void;
    id: string;
  }
  let { state: s, names, hasCard, onretry, onclose, id }: Props = $props();

  const KIND: Record<Decision['kind'], MessageKey> = {
    accepted: 'decision.kind.accepted',
    rejected: 'decision.kind.rejected',
    shadow: 'decision.kind.shadow',
    proposed: 'decision.kind.proposed',
    held: 'decision.kind.held',
    failed: 'decision.kind.failed',
  };
  const MODE: Record<Decision['mode'], MessageKey> = {
    shadow: 'mode.shadow',
    assisted: 'mode.assisted',
    automatic: 'mode.automatic',
  };
  const LAYER: Record<Decision['intent']['origin'], MessageKey> = {
    planner: 'log.layer.planner',
    controller: 'log.layer.controller',
    user: 'log.layer.user',
    safety: 'log.layer.safety',
  };
  const SOURCE: Record<string, MessageKey> = {
    solver: 'plan.badge.solver',
    heuristic: 'plan.badge.heuristic',
    default: 'plan.badge.default',
  };
  const PREFERENCE: Record<string, MessageKey> = {
    neutral: 'why.pref.neutral',
    prefer_on: 'why.pref.prefer_on',
    prefer_off: 'why.pref.prefer_off',
  };

  const name = (assetId: string): string => names.get(assetId) ?? assetId;

  function actionLines(d: Decision): string[] {
    const a = d.intent.action;
    const main = describeAction(a, tPower);
    const lines = [t(main.key, main.args)];
    if (a.kind === 'battery') for (const p of batteryParts(a, tPower)) lines.push(t(p.key, p.args));
    return lines;
  }

  const data = $derived<DecisionExplain | null>(s.status === 'ready' ? s.data : null);

  function onkeydown(e: KeyboardEvent): void {
    if (e.key === 'Escape') {
      e.stopPropagation();
      onclose();
    }
  }
</script>

<!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
<div
  {id}
  class="why"
  role="region"
  aria-label={t('why.title')}
  tabindex="-1"
  {onkeydown}
  data-testid="why-panel"
>
  {#if s.status === 'loading'}
    <p role="status" class="hint">{t('why.loading')}</p>
  {:else if s.status === 'error'}
    <p class="notice error" role="alert">
      {tDynamic(s.key, undefined, 'api.error.unknown')}
    </p>
    <button type="button" class="btn secondary" onclick={onretry}>{t('why.retry')}</button>
  {:else if data}
    {@const d = data.decision}
    <h4>{t('why.title')}</h4>
    <dl class="facts">
      <dt>{t('log.col.time')}</dt>
      <dd><time datetime={d.ts}>{tDateTime(new Date(d.ts))}</time></dd>
      <dt>{t('log.col.device')}</dt>
      <dd>{name(d.intent.asset_id)}</dd>
      <dt>{t('log.col.layer')}</dt>
      <dd>{t(LAYER[d.intent.origin])}</dd>
      <dt>{t('log.col.outcome')}</dt>
      <dd>{t(KIND[d.kind])}</dd>
      <dt>{t('why.mode')}</dt>
      <dd>{t(MODE[d.mode])}</dd>
      <dt>{t('log.col.action')}</dt>
      <dd>
        {#each actionLines(d) as line, i (i)}<span class="line">{line}</span>{/each}
      </dd>
      {#if d.observed}
        {@const o = actionLines({ ...d, intent: { ...d.intent, action: d.observed } })}
        <dt>{t('why.observed')}</dt>
        <dd>
          {#each o as line, i (i)}<span class="line">{line}</span>{/each}
        </dd>
      {/if}
    </dl>

    <h5>{t('why.reasons')}</h5>
    {#if d.reasons.length === 0}
      <p class="hint">{t('why.no_reasons')}</p>
    {:else}
      <ul class="plain">
        {#each d.reasons as r, i (i)}<li>{tDynamic(r.key, r.args)}</li>{/each}
      </ul>
    {/if}

    <h5>{t('why.plan')}</h5>
    {#if data.plan}
      {@const p = data.plan}
      <p>
        {t('why.plan_from', { time: tDateTime(new Date(p.created_at)) })}
        {#if SOURCE[p.source]}({t(SOURCE[p.source]!)}){:else}({p.source}){/if}
      </p>
      {#if p.interval}
        {@const iv = p.interval}
        <p>
          {t('why.interval', {
            start: tTime(new Date(iv.start)),
            end: tTime(new Date(iv.end)),
          })}
        </p>
        {@const band = iv.devices?.[d.intent.asset_id]}
        {#if band}
          <p data-testid="why-band">
            {band.allowed ? t('why.band.allowed') : t('why.band.blocked')}
            · {t(PREFERENCE[band.preference ?? 'neutral'] ?? 'why.pref.neutral')}
            {#if band.max_power_w != null}· {t('why.band.max', {
                power: tPower(band.max_power_w),
              })}{/if}
          </p>
        {/if}
        {#if d.intent.action.kind === 'battery' && iv.battery}
          <p>
            {iv.battery.discharge_lock ? t('why.battery.lock') : t('why.battery.free')}
            · {iv.battery.charge_from_surplus
              ? t('why.battery.surplus_on')
              : t('why.battery.surplus_off')}
            {#if iv.battery.grid_charge_max_w}· {t('why.battery.grid_charge', {
                power: tPower(iv.battery.grid_charge_max_w),
              })}{/if}
          </p>
        {/if}
        {#if iv.expected}
          {@const e = iv.expected}
          <ul class="plain inline" aria-label={t('why.expected')}>
            <li>{t('why.exp.pv', { power: tPower(e.pv_p50_w) })}</li>
            <li>{t('why.exp.load', { power: tPower(e.load_w) })}</li>
            <li>{t('why.exp.import', { power: tPower(e.import_w) })}</li>
            <li>{t('why.exp.export', { power: tPower(e.export_w) })}</li>
            {#if e.soc_pct != null}<li>{t('why.exp.soc', { value: tPercent(e.soc_pct) })}</li>{/if}
          </ul>
        {/if}
        {#if iv.reasons.length > 0}
          <ul class="plain">
            {#each iv.reasons as r, i (i)}<li>{tDynamic(r.key, r.args)}</li>{/each}
          </ul>
        {/if}
      {:else}
        <p class="hint">{t('why.plan_expired')}</p>
      {/if}
    {:else}
      <p class="hint">{t('why.plan_none')}</p>
    {/if}

    <h5>{t('why.inputs')}</h5>
    {#if data.inputs.length === 0 || !data.inputs_at}
      <p class="hint">{t('why.inputs_none')}</p>
    {:else}
      <p class="hint">
        {t('why.inputs_at', {
          time: tTime(new Date(data.inputs_at)),
          resolution: data.inputs_resolution === '1m' ? 1 : 15,
        })}
      </p>
      <table class="inputs" data-testid="why-inputs">
        <caption class="sr-only">{t('why.inputs')}</caption>
        <thead class="sr-only">
          <tr
            ><th scope="col">{t('why.inputs_name')}</th><th scope="col">{t('why.inputs_value')}</th
            ></tr
          >
        </thead>
        <tbody>
          {#each data.inputs as inp (inp.key)}
            {@const label = inputLabel(inp.key)}
            <tr>
              <th scope="row"
                >{t(label.key, { device: name(label.device ?? d.intent.asset_id) })}</th
              >
              <td
                >{inputValue(inp, {
                  power: tPower,
                  percent: tPercent,
                  number: tNumber,
                  none: t('why.no_data'),
                })}</td
              >
            </tr>
          {/each}
        </tbody>
      </table>
    {/if}

    {#if data.before.length > 0 || data.after.length > 0}
      <div class="around">
        {#each [{ key: 'why.before', rows: data.before }, { key: 'why.after', rows: data.after }] as group (group.key)}
          <div>
            <h5>{t(group.key as MessageKey)}</h5>
            {#if group.rows.length === 0}
              <p class="hint">{t('why.no_neighbours')}</p>
            {:else}
              <ul class="plain">
                {#each group.rows as n (n.id)}
                  <li>
                    <time datetime={n.decision.ts}>{tDateTime(new Date(n.decision.ts))}</time>
                    · {t(KIND[n.decision.kind])}
                    · {actionLines(n.decision)[0]}
                    {#each n.decision.reasons.slice(0, 1) as r, i (i)}
                      <span class="sub">{tDynamic(r.key, r.args)}</span>
                    {/each}
                  </li>
                {/each}
              </ul>
            {/if}
          </div>
        {/each}
      </div>
    {/if}

    <p class="links">
      {#if data.plan}
        <a href="#/plan?at={encodeURIComponent(d.ts)}" data-testid="why-plan-link"
          >{t('why.open_plan')}</a
        >
      {/if}
      {#if hasCard}
        <a href="#/?device={encodeURIComponent(d.intent.asset_id)}" data-testid="why-device-link"
          >{t('why.open_device')}</a
        >
      {/if}
      <a href={logHash({ device: d.intent.asset_id })}>{t('why.same_device')}</a>
      <button type="button" class="btn secondary" onclick={onclose}>{t('why.close')}</button>
    </p>
  {/if}
</div>

<style>
  .why {
    display: grid;
    gap: 6px;
    padding: 12px 0;
    outline: none;
  }
  h4,
  h5 {
    margin: 0;
  }
  h5 {
    margin-top: 8px;
    font-size: 0.9375rem;
  }
  p {
    margin: 0;
  }
  .facts {
    display: grid;
    grid-template-columns: max-content 1fr;
    gap: 2px 16px;
    margin: 0;
  }
  .facts dt {
    color: var(--secondary-text-color);
  }
  .facts dd {
    margin: 0;
  }
  .line {
    display: block;
  }
  .plain {
    list-style: none;
    margin: 0;
    padding: 0;
    display: grid;
    gap: 4px;
  }
  .plain.inline {
    display: flex;
    flex-wrap: wrap;
    gap: 4px 16px;
  }
  .sub {
    display: block;
    color: var(--secondary-text-color);
    font-size: 0.875rem;
  }
  .around {
    display: grid;
    gap: 8px 24px;
    grid-template-columns: repeat(auto-fit, minmax(16rem, 1fr));
  }
  .inputs {
    border-collapse: collapse;
    width: auto;
  }
  .inputs th {
    text-align: left;
    font-weight: 400;
    padding: 2px 16px 2px 0;
    color: var(--secondary-text-color);
  }
  .inputs td {
    padding: 2px 0;
    font-variant-numeric: tabular-nums;
  }
  .links {
    display: flex;
    flex-wrap: wrap;
    gap: 8px 16px;
    align-items: center;
    margin-top: 8px;
  }
</style>
